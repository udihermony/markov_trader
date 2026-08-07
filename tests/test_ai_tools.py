from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.ai.tools import execute_tool
from backend.db.models import Experiment, Instrument, PriceBar, Strategy, User

VALID_SPEC = {
    "spec_version": 2,
    "name": "sma-crossover",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "x1", "kind": "exit", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "t1"]],
}

CYCLIC_SPEC = {
    "spec_version": 2,
    "name": "cyclic",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}},
        {"id": "u2", "kind": "universe", "type": "manual_list", "params": {"tickers": ["MSFT"]}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.1}},
    ],
    "edges": [["u1", "u2"], ["u2", "u1"]],
}

ALWAYS_BUY_SPEC = {
    "spec_version": 2,
    "name": "Always Buy",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["TICK"]}},
        {"id": "t1", "kind": "trigger", "type": "always", "params": {}},
        {"id": "x1", "kind": "exit", "type": "never", "params": {}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.5}},
    ],
    "edges": [["u1", "t1"]],
}


def _make_user(db_session) -> User:
    user = User(email="tools@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_strategy(db_session, user, spec=VALID_SPEC, name="My Strategy") -> Strategy:
    strategy = Strategy(user_id=user.id, name=name, spec_json=spec, spec_version=2)
    db_session.add(strategy)
    db_session.flush()
    return strategy


def _strategy_count(db_session) -> int:
    return db_session.execute(select(func.count()).select_from(Strategy)).scalar_one()


def test_list_and_get_strategy(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user)

    listed = execute_tool("list_strategies", {}, db_session, user)
    assert isinstance(listed, list)
    assert listed[0]["id"] == strategy.id

    fetched = execute_tool("get_strategy", {"strategy_id": strategy.id}, db_session, user)
    assert fetched["name"] == "My Strategy"
    assert fetched["trust_label"] == "point_in_time"


def test_validate_strategy_valid_and_invalid(db_session):
    user = _make_user(db_session)

    valid = execute_tool("validate_strategy", {"spec": VALID_SPEC}, db_session, user)
    assert valid["valid"] is True
    assert valid["trust_label"] == "point_in_time"
    assert valid["complexity"]["label"] in {"Low", "Medium", "High"}

    invalid = execute_tool("validate_strategy", {"spec": CYCLIC_SPEC}, db_session, user)
    assert "error" in invalid


def test_create_strategy_tool_never_persists(db_session):
    user = _make_user(db_session)
    before = _strategy_count(db_session)

    result = execute_tool("create_strategy", {"name": "Proposed", "spec": VALID_SPEC}, db_session, user)

    assert result["proposal"] is True
    assert result["kind"] == "create"
    assert result["trust_label"] == "point_in_time"
    assert result["diff_summary"] == "new strategy"
    assert _strategy_count(db_session) == before


def test_update_strategy_tool_never_persists_and_computes_diff(db_session):
    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user)
    before = _strategy_count(db_session)

    changed_spec = {**VALID_SPEC, "nodes": [
        VALID_SPEC["nodes"][0], VALID_SPEC["nodes"][1], VALID_SPEC["nodes"][2],
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 0.3}},
    ]}
    result = execute_tool(
        "update_strategy", {"strategy_id": strategy.id, "spec": changed_spec}, db_session, user
    )

    assert result["proposal"] is True
    assert result["kind"] == "update"
    assert result["strategy_id"] == strategy.id
    assert "fraction: 0.1" in result["diff_summary"]
    assert result["before_spec"] == VALID_SPEC  # what Undo reverts to
    assert _strategy_count(db_session) == before  # unchanged in the DB
    assert db_session.get(Strategy, strategy.id).spec_json["nodes"][-1]["params"]["fraction"] == 0.1


def test_update_strategy_tool_unowned_returns_error_not_crash(db_session):
    user_a = _make_user(db_session)
    user_b = User(email="tools-b@example.com", password_hash="x")
    db_session.add(user_b)
    db_session.flush()
    strategy = _make_strategy(db_session, user_a)

    result = execute_tool("update_strategy", {"strategy_id": strategy.id, "spec": VALID_SPEC}, db_session, user_b)
    assert "error" in result


def _seed_bars(db_session, ticker: str, start: date, end: date) -> None:
    instrument = Instrument(ticker=ticker)
    db_session.add(instrument)
    db_session.flush()
    d = start
    while d <= end:
        if d.weekday() < 5:
            db_session.add(
                PriceBar(instrument_id=instrument.id, date=d, open=100, high=101, low=99, close=100, volume=1000)
            )
        d += timedelta(days=1)
    db_session.flush()


def test_run_backtest_tool_persists_a_real_experiment(db_session, monkeypatch):
    from backend.sources.price_bars import PriceBarsSource

    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    user = _make_user(db_session)
    strategy = _make_strategy(db_session, user, spec=ALWAYS_BUY_SPEC, name="Always Buy")

    result = execute_tool(
        "run_backtest",
        {
            "strategy_id": strategy.id, "hypothesis": "h", "expected_outcome": "e",
            "period_start": str(start), "period_end": str(end),
        },
        db_session, user,
    )

    assert "error" not in result
    assert result["result_json"]["metrics"]["n_trades"] == 1
    count = db_session.execute(select(func.count()).select_from(Experiment)).scalar_one()
    assert count == 1


def test_unknown_tool_returns_error(db_session):
    user = _make_user(db_session)
    result = execute_tool("delete_everything", {}, db_session, user)
    assert "error" in result
