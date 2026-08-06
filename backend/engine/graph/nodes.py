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
from dataclasses import dataclass
from datetime import date
from typing import Literal

from backend.engine.graph.registry import NodeTypeInfo, register_node_type
from backend.engine.graph.types import NodeContext, NodeResult
from backend.sources.registry import SourceRegistry

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


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


register_node_type(NodeTypeInfo("finviz_screen", frozenset({"universe"}), "standard", _finviz_screen_factory))
register_node_type(NodeTypeInfo("manual_list", frozenset({"universe"}), "standard", _manual_list_factory))


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


register_node_type(NodeTypeInfo("cross", frozenset({"trigger", "exit"}), "standard", _cross_factory))
register_node_type(
    NodeTypeInfo("threshold", frozenset({"trigger", "confirm", "veto"}), "standard", _threshold_factory)
)


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


register_node_type(NodeTypeInfo("time_stop", frozenset({"exit"}), "standard", _time_stop_factory))


# ---------------------------------------------------------------------- size
@dataclass(frozen=True)
class FixedFractionSizeNode:
    fraction: float

    def size(self, ctx: NodeContext) -> float:
        return ctx.portfolio.cash * self.fraction


def _fixed_fraction_factory(params: dict, registry: SourceRegistry) -> FixedFractionSizeNode:
    return FixedFractionSizeNode(fraction=params["fraction"])


register_node_type(NodeTypeInfo("fixed_fraction", frozenset({"size"}), "standard", _fixed_fraction_factory))
