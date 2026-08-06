"""The node interfaces (DESIGN.md §4.7). Nodes are stateless and pure —
`FeatureView` is the single chokepoint for data access, the successor to
`DataProvider.get_bars`, and the place the `as_of` invariant is enforced for
every source (transitively, via the M2 registry/adapters it wraps)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd

from backend.sources.expressions import parse_feature_expression
from backend.sources.registry import SourceRegistry


@dataclass(frozen=True)
class Position:
    ticker: str
    shares: int
    entry_price: float
    entry_date: date


@dataclass(frozen=True)
class PortfolioView:
    cash: float
    open_position_count: int


class FeatureView:
    """NodeContext.features — ticker+as_of-bound ergonomic wrapper around
    M2's expressions.py. Adds no new chokepoint; the as_of guard already
    lives in the registered adapters this delegates to."""

    def __init__(
        self, registry: SourceRegistry, source_aliases: dict[str, str], ticker: str, as_of: date
    ):
        self._registry = registry
        self._aliases = source_aliases
        self._ticker = ticker
        self._as_of = as_of

    def get(self, expr: str) -> float:
        return parse_feature_expression(expr).evaluate(
            self._registry, self._aliases, self._ticker, self._as_of
        )

    def get_series(
        self, expr: str, periods: int | None = 2, lookback_days: int | None = None
    ) -> pd.Series:
        series = parse_feature_expression(expr).evaluate_series(
            self._registry, self._aliases, self._ticker, self._as_of, lookback_days=lookback_days
        )
        return series.tail(periods) if periods is not None else series


@dataclass(frozen=True)
class NodeContext:
    features: FeatureView
    as_of: date
    ticker: str
    position: Position | None
    portfolio: PortfolioView


@dataclass(frozen=True)
class NodeResult:
    passed: bool
    reason: str
    explanation: str
    missing: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FunnelStageResult:
    """One stage of DESIGN.md §3's funnel view: `503 → 10 → 3 → 2 → 1`,
    plus a missing-data count where a node's source has partial coverage
    and a plain-language description of what the (already-configured) node
    does."""

    node_id: str
    kind: str
    type: str
    description: str
    candidates_before: int
    candidates_after: int
    missing_data_count: int


class UniverseNode(Protocol):
    def filter(self, candidates: list[str], as_of: date) -> list[str]: ...


class DecisionNode(Protocol):             # trigger, confirm, veto, exit
    def evaluate(self, ctx: NodeContext) -> NodeResult: ...


class SizeNode(Protocol):
    def size(self, ctx: NodeContext) -> float: ...    # cash amount, 0 = skip
