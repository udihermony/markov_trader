from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.ai.provider import ProviderResponse, ToolCall, TokenUsage
from backend.ai.unattended import run_unattended_session
from backend.db.models import Experiment, Instrument, PriceBar, Strategy, User
from backend.sources.price_bars import PriceBarsSource

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


class FakeProvider:
    def __init__(self, script: list[ProviderResponse]):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools, system) -> ProviderResponse:
        self.calls.append(messages)
        return self.script.pop(0)


def _text_response(text: str, usage: TokenUsage | None = None) -> ProviderResponse:
    return ProviderResponse(
        text=text, tool_calls=[], stop_reason="end_turn",
        raw_content=[{"type": "text", "text": text}], usage=usage or TokenUsage(),
    )


def _tool_call_response(
    name: str, tool_input: dict, call_id: str = "call_1", text: str | None = None,
    usage: TokenUsage | None = None,
) -> ProviderResponse:
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    ]
    return ProviderResponse(
        text=text, tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
        stop_reason="tool_use", raw_content=content, usage=usage or TokenUsage(),
    )


def _make_user(db_session) -> User:
    user = User(email="unattended@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()
    return user


def _make_strategy(db_session, user, spec=VALID_SPEC, name="Root") -> Strategy:
    strategy = Strategy(user_id=user.id, name=name, spec_json=spec, spec_version=2)
    db_session.add(strategy)
    db_session.flush()
    return strategy


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


def test_full_session_creates_variant_and_runs_backtest(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    user = _make_user(db_session)
    root = _make_strategy(db_session, user, spec=ALWAYS_BUY_SPEC, name="Root")

    provider = FakeProvider([
        _tool_call_response(
            "create_strategy_variant", {"name": "Variant A", "spec": ALWAYS_BUY_SPEC}, call_id="c1",
            usage=TokenUsage(100, 20),
        ),
        _tool_call_response(
            "run_backtest",
            {
                "strategy_id": root.id, "hypothesis": "h", "expected_outcome": "e",
                "period_start": str(start), "period_end": str(end),
            },
            call_id="c2", usage=TokenUsage(50, 10),
        ),
        _text_response("Ran 1 experiment. It was a dead end.", usage=TokenUsage(30, 15)),
    ])

    result = run_unattended_session(db_session, user, root.id, "find something", budget=10, provider=provider)

    variant = db_session.execute(
        select(Strategy).where(Strategy.parent_id == root.id, Strategy.created_by == "ai")
    ).scalar_one()
    assert variant.name == "Variant A"

    experiment = db_session.execute(
        select(Experiment).where(Experiment.strategy_id == root.id)
    ).scalar_one()
    assert experiment.initiated_by == "ai"

    assert result["digest"] == "Ran 1 experiment. It was a dead end."
    assert result["tokens"]["input"] == 100 + 50 + 30
    assert result["tokens"]["output"] == 20 + 10 + 15
    assert result["experiment_ids"] == [experiment.id]
    assert result["strategies_created"] == [variant.id]


def test_grade_own_prediction_sets_prediction_correct(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    user = _make_user(db_session)
    root = _make_strategy(db_session, user, spec=ALWAYS_BUY_SPEC, name="Root")

    class ScriptedProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools, system):
            self.calls += 1
            if self.calls == 1:
                return _tool_call_response(
                    "run_backtest",
                    {
                        "strategy_id": root.id, "hypothesis": "h", "expected_outcome": "e",
                        "period_start": str(start), "period_end": str(end),
                    },
                    call_id="c1",
                )
            if self.calls == 2:
                # The tool result for run_backtest is the last message's content block —
                # pull the real experiment id out of it to grade the right row.
                last = messages[-1]["content"][0]
                import json
                experiment_id = json.loads(last["content"])["id"]
                return _tool_call_response(
                    "grade_own_prediction", {"experiment_id": experiment_id, "correct": True}, call_id="c2"
                )
            return _text_response("Done.")

    provider = ScriptedProvider()
    result = run_unattended_session(db_session, user, root.id, "goal", budget=10, provider=provider)

    experiment = db_session.execute(select(Experiment).where(Experiment.strategy_id == root.id)).scalar_one()
    assert experiment.prediction_correct is True
    assert result["calibration"] == {"predicted": 1, "correct": 1}


def test_budget_refuses_further_real_calls(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    user = _make_user(db_session)
    root = _make_strategy(db_session, user, spec=ALWAYS_BUY_SPEC, name="Root")

    backtest_call = _tool_call_response(
        "run_backtest",
        {
            "strategy_id": root.id, "hypothesis": "h", "expected_outcome": "e",
            "period_start": str(start), "period_end": str(end),
        },
        call_id="c",
    )
    # budget=1: the first run_backtest succeeds, the second is refused
    provider = FakeProvider([backtest_call, backtest_call, _text_response("done")])

    run_unattended_session(db_session, user, root.id, "goal", budget=1, provider=provider)

    count = db_session.execute(select(func.count()).select_from(Experiment)).scalar_one()
    assert count == 1  # only the first backtest actually ran


def test_over_search_threshold_blocks_run_backtest(db_session):
    user = _make_user(db_session)
    root = _make_strategy(db_session, user)
    for i in range(20):
        db_session.add(
            Experiment(
                user_id=user.id, strategy_id=root.id, hypothesis=f"h{i}", expected_outcome=f"e{i}",
                actual_outcome="x", period_start=date(2026, 1, 1), period_end=date(2026, 2, 1),
                spec_snapshot_json=VALID_SPEC, result_json={"metrics": {"total_return_pct": 1.0}},
            )
        )
    db_session.flush()

    provider = FakeProvider([
        _tool_call_response(
            "run_backtest",
            {
                "strategy_id": root.id, "hypothesis": "h", "expected_outcome": "e",
                "period_start": "2026-01-01", "period_end": "2026-02-01",
            },
        ),
        _text_response("Blocked, recommending a holdout test."),
    ])

    result = run_unattended_session(db_session, user, root.id, "goal", budget=10, provider=provider)

    count = db_session.execute(select(func.count()).select_from(Experiment)).scalar_one()
    assert count == 20  # nothing new was added
    assert "holdout" in result["digest"].lower()


def test_max_turns_bound_produces_a_digest(db_session):
    user = _make_user(db_session)
    root = _make_strategy(db_session, user)

    provider = FakeProvider(
        [_tool_call_response("list_strategies", {}, call_id=f"c{i}") for i in range(50)]
    )

    result = run_unattended_session(db_session, user, root.id, "goal", budget=10, provider=provider)

    assert result["digest"]  # the forced final-turn call produced something


def test_root_not_owned_raises(db_session):
    user_a = _make_user(db_session)
    user_b = User(email="unattended-b@example.com", password_hash="x")
    db_session.add(user_b)
    db_session.flush()
    root = _make_strategy(db_session, user_a)

    with pytest.raises(HTTPException):
        run_unattended_session(db_session, user_b, root.id, "goal", budget=10, provider=FakeProvider([]))
