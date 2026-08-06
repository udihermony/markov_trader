"""Node-level tests re-verifying every invariant the deleted
test_sma_crossover.py checked, now against the `cross`/`time_stop` node API,
plus focused tests for the other four node types."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.db.models import Instrument, PriceBar
from backend.engine.graph.nodes import (
    AlwaysTriggerNode,
    CrossNode,
    FinvizScreenUniverseNode,
    FixedFractionSizeNode,
    ManualListUniverseNode,
    NeverExitNode,
    ThresholdNode,
    TimeStopExitNode,
)
from backend.engine.graph.types import FeatureView, NodeContext, PortfolioView, Position
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry


def make_bars(db_session, ticker: str, closes: list[float], start: date = date(2026, 1, 1)):
    instrument = Instrument(ticker=ticker)
    db_session.add(instrument)
    db_session.flush()
    dates, d = [], start
    while len(dates) < len(closes):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    for dt, c in zip(dates, closes):
        db_session.add(PriceBar(instrument_id=instrument.id, date=dt, open=c, high=c, low=c, close=c, volume=1_000_000))
    db_session.flush()
    return dates


def make_ctx(db_session, ticker: str, as_of: date, position=None, cash=100_000.0) -> NodeContext:
    reg = SourceRegistry()
    reg.register(PriceBarsFeatureAdapter(PriceBarsSource(db_session, DataConfig())))
    features = FeatureView(reg, {"px": "price_bars"}, ticker, as_of)
    return NodeContext(
        features=features, as_of=as_of, ticker=ticker, position=position,
        portfolio=PortfolioView(cash=cash, open_position_count=0),
    )


CROSS = CrossNode(a="sma(px.close, 3)", b="sma(px.close, 5)", direction="up")
CROSS_DOWN = CrossNode(a="sma(px.close, 3)", b="sma(px.close, 5)", direction="down")


def test_buy_on_upward_cross_event(db_session):
    closes = [100.0] * 10 + [90.0, 90.0, 130.0]
    dates = make_bars(db_session, "AAA", closes)
    ctx = make_ctx(db_session, "AAA", dates[-1])
    result = CROSS.evaluate(ctx)
    assert result.passed
    assert result.reason == "cross_up"


def test_no_buy_when_fast_merely_above_slow(db_session):
    closes = [100 + i for i in range(15)]
    dates = make_bars(db_session, "AAA", closes)
    ctx = make_ctx(db_session, "AAA", dates[-1])
    result = CROSS.evaluate(ctx)
    assert not result.passed
    assert result.reason == "no_signal"


def test_sell_on_downward_cross(db_session):
    closes = [100.0] * 10 + [110.0, 110.0, 80.0, 80.0]
    dates = make_bars(db_session, "AAA", closes)
    position = Position("AAA", 10, 100.0, dates[0])
    ctx = make_ctx(db_session, "AAA", dates[-1], position=position)
    result = CROSS_DOWN.evaluate(ctx)
    assert result.passed
    assert result.reason == "cross_down"


def test_cross_insufficient_history(db_session):
    dates = make_bars(db_session, "AAA", [100.0] * 3)
    ctx = make_ctx(db_session, "AAA", dates[-1])
    result = CROSS.evaluate(ctx)
    assert not result.passed
    assert result.reason == "insufficient_history"


TIME_STOP = TimeStopExitNode(max_hold_days=5, calendar_feature="px.close")


def test_time_stop_counts_trading_days(db_session):
    closes = [100 + i * 0.01 for i in range(20)]
    dates = make_bars(db_session, "AAA", closes)
    as_of = dates[-1]
    entry = dates[-6]  # exactly 5 trading days held
    position = Position("AAA", 10, 100.0, entry)
    ctx = make_ctx(db_session, "AAA", as_of, position=position)
    result = TIME_STOP.evaluate(ctx)
    assert result.passed
    assert result.reason == "time_stop_exit"
    assert result.metadata["days_held"] == 5


def test_no_time_stop_before_limit(db_session):
    closes = [100 + i * 0.01 for i in range(20)]
    dates = make_bars(db_session, "AAA", closes)
    as_of = dates[-1]
    entry = dates[-5]  # only 4 trading days held
    position = Position("AAA", 10, 100.0, entry)
    ctx = make_ctx(db_session, "AAA", as_of, position=position)
    result = TIME_STOP.evaluate(ctx)
    assert not result.passed


def test_time_stop_ignores_weekends(db_session):
    closes = [100 + i * 0.01 for i in range(20)]
    dates = make_bars(db_session, "AAA", closes, start=date(2026, 1, 5))  # Monday
    fridays = [d for d in dates if d.weekday() == 4]
    entry = fridays[1]
    as_of_candidates = [d for d in dates if d > entry]
    as_of = as_of_candidates[3]  # 4th trading day after entry (Thursday)
    assert (as_of - entry).days >= 6  # 6+ calendar days but only 4 trading days
    position = Position("AAA", 10, 100.0, entry)
    ctx = make_ctx(db_session, "AAA", as_of, position=position)
    result = TIME_STOP.evaluate(ctx)
    assert not result.passed


def test_time_stop_no_position_holds():
    ctx = NodeContext(
        features=None, as_of=date(2026, 1, 15), ticker="AAA", position=None,
        portfolio=PortfolioView(cash=100_000.0, open_position_count=0),
    )
    result = TIME_STOP.evaluate(ctx)
    assert not result.passed
    assert result.reason == "no_position"


def test_fixed_fraction_size(db_session):
    ctx = make_ctx(db_session, "AAA", date(2026, 1, 15), cash=50_000.0)
    node = FixedFractionSizeNode(fraction=0.10)
    assert node.size(ctx) == pytest.approx(5_000.0)


def test_threshold_node_fires(db_session):
    dates = make_bars(db_session, "AAA", [100.0] * 10)
    ctx = make_ctx(db_session, "AAA", dates[-1])
    node = ThresholdNode(feature="px.close", op=">", value=50.0)
    result = node.evaluate(ctx)
    assert result.passed
    assert result.reason == "threshold_met"


def test_threshold_node_does_not_fire(db_session):
    dates = make_bars(db_session, "AAA", [100.0] * 10)
    ctx = make_ctx(db_session, "AAA", dates[-1])
    node = ThresholdNode(feature="px.close", op=">", value=500.0)
    result = node.evaluate(ctx)
    assert not result.passed
    assert result.reason == "threshold_not_met"


def test_manual_list_universe_node():
    node = ManualListUniverseNode(tickers=["AAPL", "MSFT"])
    assert node.filter(["ignored"], date(2026, 1, 15)) == ["AAPL", "MSFT"]


def test_finviz_screen_universe_node(db_session):
    from backend.db.models import ScreenResult

    db_session.add(ScreenResult(screen_date=date(2026, 1, 15), ticker="AAPL", rank=1, source="test"))
    db_session.flush()
    screener = FinvizScreenSource(db_session, ScreenerConfig(), mode="backtest")
    node = FinvizScreenUniverseNode(FinvizScreenAdapter(screener))
    assert node.filter([], date(2026, 1, 15)) == ["AAPL"]


def test_always_trigger_node_fires(db_session):
    ctx = make_ctx(db_session, "SPY", date(2026, 1, 15))
    result = AlwaysTriggerNode().evaluate(ctx)
    assert result.passed
    assert result.reason == "always"


def test_never_exit_node_never_fires(db_session):
    position = Position("SPY", 10, 500.0, date(2026, 1, 1))
    ctx = make_ctx(db_session, "SPY", date(2026, 1, 15), position=position)
    result = NeverExitNode().evaluate(ctx)
    assert not result.passed
    assert result.reason == "never"
