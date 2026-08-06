"""Parametrized structural contract test over every registered node type:
universe types return `list[str]` from `filter`, decision types return a
`NodeResult` with all required fields from `evaluate`, the size type
returns `float` from `size`."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.db.models import Instrument, PriceBar, ScreenResult
from backend.engine.graph.registry import all_node_types
from backend.engine.graph.types import FeatureView, NodeContext, NodeResult, PortfolioView, Position
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

import backend.engine.graph.nodes  # noqa: F401  registers the 6 real node types

PARAMS_BY_TYPE = {
    "finviz_screen": {},
    "manual_list": {"tickers": ["AAPL", "MSFT"]},
    "cross": {"a": "sma(px.close, 3)", "b": "sma(px.close, 5)", "direction": "up"},
    "threshold": {"feature": "px.close", "op": ">", "value": 1.0},
    "time_stop": {"max_hold_days": 5, "calendar_feature": "px.close"},
    "fixed_fraction": {"fraction": 0.1},
}


@pytest.fixture()
def registry_and_ctx(db_session):
    instrument = Instrument(ticker="AAA")
    db_session.add(instrument)
    db_session.flush()
    d = date(2026, 1, 1)
    for i in range(15):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        db_session.add(PriceBar(instrument_id=instrument.id, date=d, open=100 + i, high=100 + i,
                                 low=100 + i, close=100 + i, volume=1000))
        d += timedelta(days=1)
    db_session.add(ScreenResult(screen_date=date(2026, 1, 15), ticker="AAA", rank=1, source="test"))
    db_session.flush()

    reg = SourceRegistry()
    reg.register(PriceBarsFeatureAdapter(PriceBarsSource(db_session, DataConfig())))
    reg.register(FinvizScreenAdapter(FinvizScreenSource(db_session, ScreenerConfig(), mode="backtest")))

    as_of = date(2026, 1, 15)
    ctx = NodeContext(
        features=FeatureView(reg, {"px": "price_bars"}, "AAA", as_of),
        as_of=as_of, ticker="AAA",
        position=Position("AAA", 10, 100.0, date(2026, 1, 1)),
        portfolio=PortfolioView(cash=100_000.0, open_position_count=1),
    )
    return reg, ctx


@pytest.mark.parametrize("type_name", sorted(PARAMS_BY_TYPE.keys()))
def test_node_contract(type_name, registry_and_ctx):
    reg, ctx = registry_and_ctx
    info = all_node_types()[type_name]
    node = info.factory(PARAMS_BY_TYPE[type_name], reg)

    has_filter = hasattr(node, "filter")
    has_evaluate = hasattr(node, "evaluate")
    has_size = hasattr(node, "size")
    assert sum([has_filter, has_evaluate, has_size]) == 1, "a node implements exactly one Protocol"

    if has_filter:
        result = node.filter([], ctx.as_of)
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)
    elif has_evaluate:
        result = node.evaluate(ctx)
        assert isinstance(result, NodeResult)
        assert isinstance(result.passed, bool)
        assert isinstance(result.reason, str)
        assert isinstance(result.explanation, str)
        assert isinstance(result.missing, list)
        assert isinstance(result.metadata, dict)
    else:
        result = node.size(ctx)
        assert isinstance(result, float)


def test_all_six_real_node_types_registered():
    # Other test modules register throwaway fake types into the same
    # process-wide registry (test_graph_validator.py, test_graph_compiled.py)
    # — check the real 6 are a subset, not an exact match.
    assert set(PARAMS_BY_TYPE.keys()) <= set(all_node_types().keys())
