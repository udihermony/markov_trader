from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.db.models import Instrument, PriceBar, ScreenResult
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.registry import NodeTypeInfo, register_node_type
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.engine.graph.types import NodeContext, NodeResult, PortfolioView, Position
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import (
    AlignmentPolicy,
    FeatureSpec,
    SourceRegistry,
    SourceSpec,
    TrustClass,
)

import backend.engine.graph.nodes  # noqa: F401  registers the 6 real node types


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


def build_registry(db_session) -> SourceRegistry:
    reg = SourceRegistry()
    price_bars = PriceBarsSource(db_session, DataConfig())
    reg.register(PriceBarsFeatureAdapter(price_bars))
    screener = FinvizScreenSource(db_session, ScreenerConfig(), mode="backtest")
    reg.register(FinvizScreenAdapter(screener))
    return reg


def make_spec(tickers: list[str]) -> StrategySpec:
    return StrategySpec(
        name="test-spec",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": tickers}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": "sma(px.close, 3)", "b": "sma(px.close, 5)", "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="cross",
                     params={"a": "sma(px.close, 3)", "b": "sma(px.close, 5)", "direction": "down"}),
            NodeSpec(id="x2", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.2}),
        ],
        edges=[("u1", "t1")],
    )


def test_candidates_returns_universe_list(db_session):
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAPL", "MSFT"]), reg)
    assert graph.candidates(date(2026, 1, 15)) == ["AAPL", "MSFT"]


def test_evaluate_entry_passes_on_upward_cross(db_session):
    closes = [100.0] * 10 + [90.0, 90.0, 130.0]
    dates = make_bars(db_session, "AAA", closes)
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    portfolio = PortfolioView(cash=100_000.0, open_position_count=0)
    result = graph.evaluate_entry("AAA", dates[-1], portfolio)
    assert result.passed
    assert result.reason == "cross_up"


def test_evaluate_entry_fails_without_signal(db_session):
    dates = make_bars(db_session, "AAA", [100 + i for i in range(15)])
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    portfolio = PortfolioView(cash=100_000.0, open_position_count=0)
    result = graph.evaluate_entry("AAA", dates[-1], portfolio)
    assert not result.passed


def test_evaluate_exit_fires_on_downward_cross(db_session):
    closes = [100.0] * 10 + [110.0, 110.0, 80.0, 80.0]
    dates = make_bars(db_session, "AAA", closes)
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    position = Position("AAA", 10, 100.0, dates[0])
    portfolio = PortfolioView(cash=90_000.0, open_position_count=1)
    result = graph.evaluate_exit("AAA", position, dates[-1], portfolio)
    assert result is not None
    assert result.reason == "cross_down"


def test_evaluate_exit_returns_none_when_nothing_fires(db_session):
    dates = make_bars(db_session, "AAA", [100 + i * 0.01 for i in range(10)])
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    position = Position("AAA", 10, 100.0, dates[-2])  # just entered, no exit should fire
    portfolio = PortfolioView(cash=90_000.0, open_position_count=1)
    result = graph.evaluate_exit("AAA", position, dates[-1], portfolio)
    assert result is None


def test_size_returns_cash_times_fraction(db_session):
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    portfolio = PortfolioView(cash=10_000.0, open_position_count=0)
    assert graph.size("AAA", date(2026, 1, 15), portfolio) == pytest.approx(2_000.0)


# --- on_missing behavior ---


class _AlwaysMissingNode:
    def evaluate(self, ctx: NodeContext) -> NodeResult:
        return NodeResult(passed=False, reason="always_missing", explanation="test node",
                           missing=["fake.feature"])


def _always_missing_factory(params: dict, registry):
    return _AlwaysMissingNode()


register_node_type(NodeTypeInfo("always_missing", frozenset({"trigger"}), "standard", _always_missing_factory))


def _spec_with_on_missing(policy: str) -> StrategySpec:
    return StrategySpec(
        name="on-missing-test",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAA"]}),
            NodeSpec(id="t1", kind="trigger", type="always_missing", params={}, on_missing=policy),
            NodeSpec(id="x1", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "t1")],
    )


def test_on_missing_fail_open_passes(db_session):
    reg = build_registry(db_session)
    graph = CompiledGraph(_spec_with_on_missing("fail_open"), reg)
    portfolio = PortfolioView(cash=100_000.0, open_position_count=0)
    result = graph.evaluate_entry("AAA", date(2026, 1, 15), portfolio)
    assert result.passed


def test_on_missing_fail_closed_fails(db_session):
    reg = build_registry(db_session)
    graph = CompiledGraph(_spec_with_on_missing("fail_closed"), reg)
    portfolio = PortfolioView(cash=100_000.0, open_position_count=0)
    result = graph.evaluate_entry("AAA", date(2026, 1, 15), portfolio)
    assert not result.passed


# --- evaluate_funnel (M6) ---


def test_evaluate_funnel_narrows_candidates_stage_by_stage(db_session):
    # AAA crosses up; BBB stays flat (no signal).
    aaa_closes = [100.0] * 10 + [90.0, 90.0, 130.0]
    bbb_closes = [100.0] * 13
    make_bars(db_session, "AAA", aaa_closes)
    dates = make_bars(db_session, "BBB", bbb_closes)
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA", "BBB"]), reg)

    stages = graph.evaluate_funnel(dates[-1])

    universe_stage = next(s for s in stages if s.kind == "universe")
    assert universe_stage.candidates_before == 0
    assert universe_stage.candidates_after == 2

    trigger_stage = next(s for s in stages if s.kind == "trigger")
    assert trigger_stage.candidates_before == 2
    assert trigger_stage.candidates_after == 1  # only AAA survives
    assert trigger_stage.description  # plain-language sentence present
    assert trigger_stage.missing_data_count == 0


def test_evaluate_funnel_counts_missing_data(db_session):
    spec = StrategySpec(
        name="missing-data-test",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAA"]}),
            NodeSpec(id="t1", kind="trigger", type="always_missing", params={}),
            NodeSpec(id="x1", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "t1")],
    )
    reg = build_registry(db_session)
    graph = CompiledGraph(spec, reg)

    stages = graph.evaluate_funnel(date(2026, 1, 15))

    trigger_stage = next(s for s in stages if s.kind == "trigger")
    assert trigger_stage.missing_data_count == 1
    assert trigger_stage.candidates_after == 0  # no on_missing policy set -> raw passed=False stands


def test_trust_label_reflects_weakest_source(db_session):
    reg = build_registry(db_session)

    class _FakeWeakAdapter:
        spec = SourceSpec(
            id="fake_reconstructable",
            features={"value": FeatureSpec("value", "float")},
            trust_class=TrustClass.RECONSTRUCTABLE,
            native_frequency="daily",
            alignment=AlignmentPolicy(native_frequency="daily"),
            coverage_note="test double",
        )

    reg.register(_FakeWeakAdapter())

    spec = StrategySpec(
        name="weak-source-test",
        sources=[
            SourceRef(id="px", type="price_bars"),
            SourceRef(id="weak", type="fake_reconstructable"),
        ],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAA"]}),
            NodeSpec(id="t1", kind="trigger", type="always", params={}),
            NodeSpec(id="x1", kind="exit", type="never", params={}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "t1")],
    )
    graph = CompiledGraph(spec, reg)
    assert graph.trust_label == TrustClass.RECONSTRUCTABLE  # weaker than price_bars' point_in_time


def test_trust_label_point_in_time_when_only_strong_sources(db_session):
    reg = build_registry(db_session)
    graph = CompiledGraph(make_spec(["AAA"]), reg)
    assert graph.trust_label == TrustClass.POINT_IN_TIME
