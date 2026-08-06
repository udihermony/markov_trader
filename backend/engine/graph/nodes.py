"""The minimal M3 node library (DESIGN.md §4.8, scoped down): two universe
types, two generic types, one exit type beyond the reused generic `cross`,
one size type. Enough to express the SMA-crossover strategy as a graph and
prove the registry/validator/evaluator generalizes beyond one hardcoded
case. Composite convenience nodes, `index_membership`, and the other
size/exit types are deferred to when the funnel builder (M6) or Lab (M7)
actually needs them.
"""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from backend.engine.graph.registry import NodeTypeInfo, ParamField, register_node_type
from backend.engine.graph.types import NodeContext, NodeResult
from backend.sources.registry import SourceRegistry

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}
_OP_WORDS = {"<": "below", "<=": "at or below", ">": "above", ">=": "at or above", "==": "equal to"}

# M6's funnel builder composes "expression" param fields as a function +
# window picker (e.g. "10-day average"), never raw expression syntax —
# CLAUDE.md's no-jargon rule. This is the plain-language side of that:
# turning "sma(px.close, 10)" back into "the 10-day average" for a node's
# `describe()` sentence. Only against px.close, since price_bars is still
# the only source with real data (see plan's M6 context).
_EXPR_CALL_RE = re.compile(r"^(\w+)\(px\.close,\s*(\d+)\)$")
_FUNCTION_DESCRIPTIONS = {
    "sma": "day average",
    "rolling_mean": "day average",
    "ema": "day exponential average",
    "rsi": "day RSI",
    "pct_change": "day price change",
    "zscore": "day z-score",
    "rank": "day rank",
}


def _describe_feature_expr(expr: str) -> str:
    """Returns a bare noun phrase (no leading article) — call sites decide
    whether "the"/"The" reads naturally in their sentence."""
    if expr.strip() == "px.close":
        return "price"
    match = _EXPR_CALL_RE.match(expr.strip())
    if match:
        func, window = match.group(1), match.group(2)
        suffix = _FUNCTION_DESCRIPTIONS.get(func)
        if suffix:
            return f"{window}-{suffix}"
    return expr  # fallback for anything unrecognized — better than a blank sentence


# ------------------------------------------------------------------ universe
@dataclass(frozen=True)
class FinvizScreenUniverseNode:
    adapter: object  # a FinvizScreenAdapter — ignores `candidates`, it generates the list

    def filter(self, candidates: list[str], as_of: date) -> list[str]:
        return self.adapter.get_watchlist_as_of(as_of)


@dataclass(frozen=True)
class ManualListUniverseNode:
    tickers: list[str]  # also ignores `candidates` — a fixed generating list, not a filter

    def filter(self, candidates: list[str], as_of: date) -> list[str]:
        return list(self.tickers)


def _finviz_screen_factory(params: dict, registry: SourceRegistry) -> FinvizScreenUniverseNode:
    _, adapter = registry.get("finviz_screen")
    return FinvizScreenUniverseNode(adapter)


def _manual_list_factory(params: dict, registry: SourceRegistry) -> ManualListUniverseNode:
    return ManualListUniverseNode(tickers=list(params["tickers"]))


register_node_type(NodeTypeInfo(
    "finviz_screen", frozenset({"universe"}), "standard", _finviz_screen_factory,
    params_schema=[],
    describe=lambda params: "Screens for liquid S&P 500 stocks.",
))
register_node_type(NodeTypeInfo(
    "manual_list", frozenset({"universe"}), "standard", _manual_list_factory,
    params_schema=[ParamField("tickers", "ticker_list", "Tickers to watch")],
    describe=lambda params: f"Watches only: {', '.join(params.get('tickers', []))}.",
))


# ------------------------------------------------------------------- generic
@dataclass(frozen=True)
class CrossNode:
    """Trigger or exit, per DESIGN.md's example spec (same `type`, different
    `kind`/`direction`). Fires on the cross *event* — a_prev<=b_prev and
    a_now>b_now for "up" (the mirror for "down") — never a level comparison.
    Identical semantics to the retired SmaCrossover's crossover checks."""

    a: str
    b: str
    direction: Literal["up", "down"]

    def evaluate(self, ctx: NodeContext) -> NodeResult:
        a_series = ctx.features.get_series(self.a, periods=2)
        b_series = ctx.features.get_series(self.b, periods=2)
        if (
            len(a_series) < 2 or len(b_series) < 2
            or a_series.isna().any() or b_series.isna().any()
        ):
            return NodeResult(
                passed=False, reason="insufficient_history",
                explanation="Not enough price history to compute the crossover yet.",
            )
        a_prev, a_now = float(a_series.iloc[-2]), float(a_series.iloc[-1])
        b_prev, b_now = float(b_series.iloc[-2]), float(b_series.iloc[-1])
        if self.direction == "up":
            fired = a_prev <= b_prev and a_now > b_now
            fired_reason = "cross_up"
        else:
            fired = a_prev >= b_prev and a_now < b_now
            fired_reason = "cross_down"
        return NodeResult(
            passed=fired,
            reason=fired_reason if fired else "no_signal",
            explanation=(
                f"{self.a} crossed {'above' if self.direction == 'up' else 'below'} {self.b}."
                if fired else "No crossover."
            ),
            metadata={"a_now": a_now, "b_now": b_now},
        )


@dataclass(frozen=True)
class ThresholdNode:
    feature: str
    op: Literal["<", "<=", ">", ">=", "=="]
    value: float

    def evaluate(self, ctx: NodeContext) -> NodeResult:
        val = ctx.features.get(self.feature)
        if val != val:  # NaN
            return NodeResult(
                passed=False, reason="missing_feature", explanation="Feature value unavailable.",
                missing=[self.feature],
            )
        fired = _OPS[self.op](val, self.value)
        return NodeResult(
            passed=fired,
            reason="threshold_met" if fired else "threshold_not_met",
            explanation=f"{self.feature} {self.op} {self.value}: {'yes' if fired else 'no'} (value={val:.4f}).",
            metadata={"value": val},
        )


def _cross_factory(params: dict, registry: SourceRegistry) -> CrossNode:
    return CrossNode(a=params["a"], b=params["b"], direction=params["direction"])


def _threshold_factory(params: dict, registry: SourceRegistry) -> ThresholdNode:
    return ThresholdNode(feature=params["feature"], op=params["op"], value=params["value"])


register_node_type(NodeTypeInfo(
    "cross", frozenset({"trigger", "exit"}), "standard", _cross_factory,
    params_schema=[
        ParamField("a", "expression", "First value"),
        ParamField("b", "expression", "Second value"),
        ParamField("direction", "enum", "Direction", options=["up", "down"]),
    ],
    describe=lambda params: (
        f"The {_describe_feature_expr(params['a'])} crosses "
        f"{'above' if params['direction'] == 'up' else 'below'} "
        f"the {_describe_feature_expr(params['b'])}."
    ),
))
register_node_type(NodeTypeInfo(
    "threshold", frozenset({"trigger", "confirm", "veto"}), "standard", _threshold_factory,
    params_schema=[
        ParamField("feature", "expression", "Value to check"),
        ParamField("op", "enum", "Comparison", options=list(_OPS.keys())),
        ParamField("value", "number", "Threshold"),
    ],
    describe=lambda params: (
        f"The {_describe_feature_expr(params['feature'])} is {_OP_WORDS[params['op']]} {params['value']}."
    ),
))


# ---------------------------------------------------------------------- exit
@dataclass(frozen=True)
class TimeStopExitNode:
    """Identical logic to the retired SmaCrossover's time stop — trading
    days strictly after entry through as_of, counted from a feature's
    series index, not calendar days. `calendar_feature` is an explicit raw
    feature expression (e.g. "px.close") whose index defines the trading
    calendar, rather than a hardcoded source alias — consistent with every
    other node driving its data access through explicit params."""

    max_hold_days: int
    calendar_feature: str

    def evaluate(self, ctx: NodeContext) -> NodeResult:
        if ctx.position is None:
            return NodeResult(passed=False, reason="no_position", explanation="No open position.")
        lookback = (ctx.as_of - ctx.position.entry_date).days + 10
        series = ctx.features.get_series(self.calendar_feature, periods=None, lookback_days=lookback)
        held = sum(1 for d in series.index if ctx.position.entry_date < d <= ctx.as_of)
        if held >= self.max_hold_days:
            return NodeResult(
                passed=True, reason="time_stop_exit",
                explanation=f"Held for {held} trading days, at or past the {self.max_hold_days}-day limit.",
                metadata={"days_held": held},
            )
        return NodeResult(
            passed=False, reason="holding", explanation=f"Held for {held} trading days.",
            metadata={"days_held": held},
        )


def _time_stop_factory(params: dict, registry: SourceRegistry) -> TimeStopExitNode:
    return TimeStopExitNode(max_hold_days=params["max_hold_days"], calendar_feature=params["calendar_feature"])


register_node_type(NodeTypeInfo(
    "time_stop", frozenset({"exit"}), "standard", _time_stop_factory,
    # `calendar_feature` is deliberately NOT in the form schema — "what
    # defines the trading calendar" is implementation plumbing, not a
    # concept a non-trader user should have to think about (CLAUDE.md: "if
    # a concept can't be explained in one sentence, it doesn't get a
    # control"). The frontend always injects calendar_feature="px.close"
    # itself when assembling this node's params before saving/previewing.
    params_schema=[ParamField("max_hold_days", "number", "Max holding days", default=5)],
    describe=lambda params: f"Automatically exits after {params['max_hold_days']} trading days.",
))


# ------------------------------------------------------- buy-and-hold primitives
@dataclass(frozen=True)
class AlwaysTriggerNode:
    """Fires unconditionally. Needed by the default SPY buy-and-hold wallet
    (M4) — buy-and-hold has no natural trigger condition in the rest of the
    vocabulary, and this is clearer than faking one with `threshold` against
    a condition that's always true by construction (e.g. price > 0)."""

    def evaluate(self, ctx: NodeContext) -> NodeResult:
        return NodeResult(passed=True, reason="always", explanation="Always fires.")


@dataclass(frozen=True)
class NeverExitNode:
    """Never fires. The buy-and-hold counterpart to `AlwaysTriggerNode` —
    every graph needs at least one exit node (DESIGN.md §4.5), and a wallet
    that's meant to never sell needs one that's honest about never firing."""

    def evaluate(self, ctx: NodeContext) -> NodeResult:
        return NodeResult(passed=False, reason="never", explanation="Never fires.")


def _always_factory(params: dict, registry: SourceRegistry) -> AlwaysTriggerNode:
    return AlwaysTriggerNode()


def _never_factory(params: dict, registry: SourceRegistry) -> NeverExitNode:
    return NeverExitNode()


register_node_type(NodeTypeInfo(
    "always", frozenset({"trigger"}), "standard", _always_factory,
    params_schema=[], describe=lambda params: "Always proceeds.",
))
register_node_type(NodeTypeInfo(
    "never", frozenset({"exit"}), "standard", _never_factory,
    params_schema=[], describe=lambda params: "Never fires.",
))


# ---------------------------------------------------------------------- size
@dataclass(frozen=True)
class FixedFractionSizeNode:
    fraction: float

    def size(self, ctx: NodeContext) -> float:
        return ctx.portfolio.cash * self.fraction


def _fixed_fraction_factory(params: dict, registry: SourceRegistry) -> FixedFractionSizeNode:
    return FixedFractionSizeNode(fraction=params["fraction"])


register_node_type(NodeTypeInfo(
    "fixed_fraction", frozenset({"size"}), "standard", _fixed_fraction_factory,
    params_schema=[ParamField("fraction", "number", "Fraction of cash per trade", default=0.1)],
    describe=lambda params: f"Invests {params['fraction'] * 100:.0f}% of available cash per trade.",
))
