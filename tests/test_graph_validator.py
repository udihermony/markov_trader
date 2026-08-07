from __future__ import annotations

import pytest

from backend.engine.graph.registry import NodeTypeInfo, register_node_type
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.engine.graph.validator import GraphValidationError, validate_spec

# import for the side-effect of registering the 6 real node types
import backend.engine.graph.nodes  # noqa: F401


def make_valid_spec() -> StrategySpec:
    return StrategySpec(
        name="valid",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAPL"]}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="cross",
                     params={"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "down"}),
            NodeSpec(id="x2", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "t1")],
    )


def test_valid_spec_passes():
    validate_spec(make_valid_spec())  # no raise


def test_cycle_detected():
    spec = StrategySpec(
        name="cyclic",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAPL"]}),
            NodeSpec(id="u2", kind="universe", type="manual_list", params={"tickers": ["MSFT"]}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "u2"), ("u2", "u1")],
    )
    with pytest.raises(GraphValidationError, match="cycle"):
        validate_spec(spec)


def test_backward_edge_rejected():
    spec = StrategySpec(
        name="backward",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAPL"]}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}),
            NodeSpec(id="c1", kind="confirm", type="threshold",
                     params={"feature": "px.close", "op": ">", "value": 1}),
            NodeSpec(id="x1", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": 0.1}),
        ],
        edges=[("u1", "t1"), ("t1", "c1"), ("c1", "t1")],
    )
    with pytest.raises(GraphValidationError, match="backward"):
        validate_spec(spec)


def test_missing_required_kind_rejected():
    spec = StrategySpec(
        name="no-size",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="manual_list", params={"tickers": ["AAPL"]}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": "sma(px.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="time_stop",
                     params={"max_hold_days": 5, "calendar_feature": "px.close"}),
        ],
        edges=[("u1", "t1")],
    )
    with pytest.raises(GraphValidationError, match="missing a required"):
        validate_spec(spec)


def test_exit_node_with_edge_rejected():
    spec = make_valid_spec()
    spec = spec.model_copy(update={"edges": [*spec.edges, ("x1", "s1")]})
    with pytest.raises(GraphValidationError, match="unwired"):
        validate_spec(spec)


def test_undeclared_alias_rejected():
    spec = make_valid_spec()
    nodes = list(spec.nodes)
    nodes[1] = nodes[1].model_copy(
        update={"params": {"a": "sma(bogus.close, 10)", "b": "sma(px.close, 20)", "direction": "up"}}
    )
    spec = spec.model_copy(update={"nodes": nodes})
    with pytest.raises(GraphValidationError, match="unknown source alias"):
        validate_spec(spec)


def test_ai_in_trigger_rejected():
    # A throwaway fake AI-maturity node type, registered only to prove the
    # rule fires — mirrors M2's "deliberately broken adapter" pattern.
    register_node_type(
        NodeTypeInfo("fake_ai_veto", frozenset({"trigger", "veto"}), "AI", lambda params, registry: object())
    )
    spec = make_valid_spec()
    nodes = list(spec.nodes)
    nodes[1] = nodes[1].model_copy(update={"type": "fake_ai_veto", "params": {}})
    spec = spec.model_copy(update={"nodes": nodes})
    with pytest.raises(GraphValidationError, match="AI-backed"):
        validate_spec(spec)


def test_ai_rejected_in_confirm_even_with_looser_allowed_kinds():
    # allowed_kinds alone permits confirm here — proves the maturity check
    # is a second, independent line of defense (same shape as the trigger
    # test above), not something a node type's allowed_kinds could bypass.
    register_node_type(
        NodeTypeInfo("fake_ai_confirm", frozenset({"confirm", "veto"}), "AI", lambda params, registry: object())
    )
    spec = make_valid_spec()
    nodes = [*spec.nodes, NodeSpec(id="c1", kind="confirm", type="fake_ai_confirm", params={})]
    spec = spec.model_copy(update={"nodes": nodes, "edges": [*spec.edges, ("t1", "c1")]})
    with pytest.raises(GraphValidationError, match="AI-backed"):
        validate_spec(spec)


def test_ai_news_check_rejected_in_confirm_by_allowed_kinds():
    # ai_news_check's own allowed_kinds is {"veto"} — confirm is already
    # rejected before the AI-specific check even runs.
    spec = make_valid_spec()
    nodes = [*spec.nodes, NodeSpec(id="c1", kind="confirm", type="ai_news_check", params={})]
    spec = spec.model_copy(update={"nodes": nodes, "edges": [*spec.edges, ("t1", "c1")]})
    with pytest.raises(GraphValidationError, match="not allowed in kind"):
        validate_spec(spec)


def test_ai_regime_check_allowed_in_veto():
    spec = make_valid_spec()
    nodes = [*spec.nodes, NodeSpec(id="v1", kind="veto", type="ai_regime_check", params={})]
    sources = [*spec.sources, SourceRef(id="ai", type="ai_judgment")]
    spec = spec.model_copy(update={"nodes": nodes, "sources": sources, "edges": [*spec.edges, ("t1", "v1")]})
    validate_spec(spec)  # no raise


def test_ai_node_without_declared_source_rejected():
    spec = make_valid_spec()
    nodes = [*spec.nodes, NodeSpec(id="v1", kind="veto", type="ai_regime_check", params={})]
    spec = spec.model_copy(update={"nodes": nodes, "edges": [*spec.edges, ("t1", "v1")]})
    with pytest.raises(GraphValidationError, match="ai_judgment"):
        validate_spec(spec)
