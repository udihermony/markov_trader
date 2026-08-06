"""StrategySpec parses DESIGN.md §4.6's example JSON shape correctly. This
is a structural round-trip test only — that example uses node *types* M3
doesn't implement (e.g. `index_membership`, `market_regime`); the spec model
itself doesn't know or care which types exist, only the *validator* does."""
from __future__ import annotations

from backend.engine.graph.spec import StrategySpec

DESIGN_EXAMPLE = {
    "spec_version": 2,
    "name": "Momentum swing",
    "sources": [
        {"id": "px", "type": "price_bars"},
        {"id": "sent", "type": "x_firehose", "params": {"window_days": 7}},
        {"id": "pm", "type": "polymarket"},
    ],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "index_membership", "params": {"index": "SP500"}},
        {"id": "u2", "kind": "universe", "type": "liquidity_filter",
         "params": {"min_price": 20, "min_avg_volume": 500000}},
        {"id": "t1", "kind": "trigger", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}},
        {"id": "c1", "kind": "confirm", "type": "threshold",
         "params": {"feature": "zscore(sent.sentiment, 30)", "op": ">", "value": 0.5},
         "on_missing": "fail_open"},
        {"id": "c2", "kind": "confirm", "type": "market_regime",
         "params": {"benchmark": "SPY", "condition": "above", "feature": "sma(px.close, 200)"}},
        {"id": "v1", "kind": "veto", "type": "earnings_blackout",
         "params": {"days_before": 3, "days_after": 1}, "on_missing": "fail_closed"},
        {"id": "v2", "kind": "veto", "type": "threshold",
         "params": {"feature": "pm.prob", "op": "<", "value": 0.4}, "on_missing": "fail_open"},
        {"id": "x1", "kind": "exit", "type": "cross",
         "params": {"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}},
        {"id": "x2", "kind": "exit", "type": "time_stop", "params": {"max_hold_days": 5}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction",
         "params": {"fraction": 0.10, "max_positions": 8, "min_notional": 500}},
    ],
    "edges": [["u1", "u2"], ["u2", "t1"], ["t1", "c1"], ["c1", "c2"],
              ["c2", "v1"], ["v1", "v2"], ["v2", "s1"]],
    "costs": {"slippage_bps": 5},
}


def test_parses_design_doc_example_verbatim():
    spec = StrategySpec.model_validate(DESIGN_EXAMPLE)
    assert spec.spec_version == 2
    assert spec.name == "Momentum swing"
    assert len(spec.sources) == 3
    assert len(spec.nodes) == 10
    assert len(spec.edges) == 7
    assert spec.costs == {"slippage_bps": 5}


def test_node_kinds_are_constrained():
    spec = StrategySpec.model_validate(DESIGN_EXAMPLE)
    kinds = {n.kind for n in spec.nodes}
    assert kinds == {"universe", "trigger", "confirm", "veto", "exit", "size"}


def test_score_kind_not_accepted_v1():
    import pytest
    from pydantic import ValidationError

    bad = {**DESIGN_EXAMPLE, "nodes": [{"id": "z1", "kind": "score", "type": "weighted", "params": {}}]}
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(bad)


def test_minimal_spec_defaults():
    spec = StrategySpec(
        name="minimal",
        sources=[{"id": "px", "type": "price_bars"}],
        nodes=[{"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["AAPL"]}}],
    )
    assert spec.edges == []
    assert spec.costs == {}
    assert spec.spec_version == 2
