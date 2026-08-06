from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.db.models import EquitySnapshot, Fill, Instrument, Order, Position, PriceBar, Wallet
from backend.engine.backtest_runner import (
    calibrated_entry_probability,
    luck_baseline,
    run_ephemeral_backtest,
)
from backend.engine.graph.spec import StrategySpec
from backend.sources.price_bars import PriceBarsSource

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


def _row_counts(db_session) -> dict[str, int]:
    return {
        "wallets": db_session.execute(select(func.count()).select_from(Wallet)).scalar_one(),
        "orders": db_session.execute(select(func.count()).select_from(Order)).scalar_one(),
        "fills": db_session.execute(select(func.count()).select_from(Fill)).scalar_one(),
        "positions": db_session.execute(select(func.count()).select_from(Position)).scalar_one(),
        "equity_snapshots": db_session.execute(select(func.count()).select_from(EquitySnapshot)).scalar_one(),
    }


def test_run_ephemeral_backtest_produces_a_fill_and_metrics(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    spec = StrategySpec.model_validate(ALWAYS_BUY_SPEC)
    result = run_ephemeral_backtest(spec, start, end, connection=db_session.connection())

    assert result.metrics.n_trades == 1
    assert result.fills[0]["ticker"] == "TICK"
    assert result.fills[0]["action"] == "BUY"
    assert len(result.equity_curve) > 0
    # flat prices + slippage against the buyer -> a small negative return, never a gain
    assert result.metrics.total_return_pct <= 0


def test_run_ephemeral_backtest_leaves_no_rows_behind(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    before = _row_counts(db_session)
    spec = StrategySpec.model_validate(ALWAYS_BUY_SPEC)
    run_ephemeral_backtest(spec, start, end, connection=db_session.connection())
    after = _row_counts(db_session)

    assert before == after == {"wallets": 0, "orders": 0, "fills": 0, "positions": 0, "equity_snapshots": 0}


def test_run_ephemeral_backtest_leaves_no_rows_behind_even_on_error(db_session, monkeypatch):
    monkeypatch.setattr(PriceBarsSource, "_refresh_one", lambda self, ticker, start, end: None)
    start, end = date(2026, 2, 2), date(2026, 3, 6)
    _seed_bars(db_session, "TICK", start - timedelta(days=120), end)
    _seed_bars(db_session, "SPY", start - timedelta(days=120), end)

    def _boom(self, as_of, quiet=False):
        raise RuntimeError("boom")

    from backend.engine.orchestrator import Orchestrator

    monkeypatch.setattr(Orchestrator, "run_day", _boom)

    before = _row_counts(db_session)
    spec = StrategySpec.model_validate(ALWAYS_BUY_SPEC)
    with pytest.raises(RuntimeError):
        run_ephemeral_backtest(spec, start, end, connection=db_session.connection())
    after = _row_counts(db_session)

    assert before == after == {"wallets": 0, "orders": 0, "fills": 0, "positions": 0, "equity_snapshots": 0}


def test_calibrated_entry_probability():
    assert calibrated_entry_probability(0, 100) == 0.0
    assert calibrated_entry_probability(10, 100) == pytest.approx(0.1)
    assert calibrated_entry_probability(200, 100) == 1.0  # clamped
    assert calibrated_entry_probability(5, 0) == 0.0


def test_luck_baseline_rises_toward_the_top_as_search_count_grows():
    null = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # one try is just a typical single draw -> the bottom of "top 1/1 = everything"
    assert luck_baseline(null, k=1) == 1.0
    # more tries -> chance alone climbs toward the top of the noise
    assert luck_baseline(null, k=5) > luck_baseline(null, k=1)
    assert luck_baseline(null, k=10) == 9.0
    assert luck_baseline([], k=5) is None
    assert luck_baseline(null, k=0) is None
