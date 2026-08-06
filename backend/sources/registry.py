"""The source registry — the generic notion of a "source" that publishes
named features into a namespace (DESIGN.md §4.1).

Registration is explicit, not an import-time side effect: adapter modules
expose a `register_*_source(adapter)` function, and the *caller* (a test
fixture, or later the orchestrator's setup) constructs the adapter with its
own session and registers it. A global import-time `registry.register(...)`
call would make registration order implicit and complicate test isolation —
two tests constructing different `PriceBarsSource` instances with different
sessions would silently clobber the same global entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Protocol

import pandas as pd


class TrustClass(str, Enum):
    POINT_IN_TIME = "point_in_time"
    RECONSTRUCTABLE = "reconstructable"
    LIVE_ONLY = "live_only"


@dataclass(frozen=True)
class FeatureSpec:
    name: str          # "close" — the part after the dot in "px.close"
    dtype: str          # "float" | "int" | "date"


@dataclass(frozen=True)
class AlignmentPolicy:
    """DESIGN.md §4.3: a feature's value on day t is its last observation
    strictly before day t's close. For `native_frequency="daily"` sources
    this collapses to the existing `date <= as_of` filter every adapter
    already implements. `"intraday"`/`"event"` sources need a real
    `observed_at`-timestamp join against sub-daily data — no such source
    exists until M11 (x_firehose, polymarket), so that join isn't
    implemented yet; declaring the type now means M11 won't need a type
    migration when it lands.
    """

    native_frequency: str  # "daily" | "intraday" | "event"

    def join_for(self, native_frequency: str) -> None:
        if native_frequency != "daily":
            raise NotImplementedError(
                "intraday/event alignment join is not implemented — no source "
                "with sub-daily resolution exists yet (see M11 in DESIGN.md §9)"
            )


@dataclass(frozen=True)
class SourceSpec:
    id: str                              # "price_bars", "finviz_screen"
    features: dict[str, FeatureSpec]      # {} for sources with no feature-expression surface
    trust_class: TrustClass
    native_frequency: str                  # "daily" | "intraday" | "event"
    alignment: AlignmentPolicy
    coverage_note: str                      # CLAUDE.md's "missing-data policy" — a static
                                             # declared-gap string. Per-node on_missing
                                             # (fail_open/fail_closed) is a node-level concept
                                             # (DESIGN.md §4.3) that belongs to M3's node spec.


class SourceAdapter(Protocol):
    spec: SourceSpec

    def get_series(
        self, feature: str, ticker: str, as_of: date, lookback_days: int
    ) -> pd.Series: ...


class SourceRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[SourceSpec, object]] = {}

    def register(self, adapter: object) -> None:
        spec: SourceSpec = adapter.spec  # type: ignore[attr-defined]
        self._entries[spec.id] = (spec, adapter)

    def get(self, source_id: str) -> tuple[SourceSpec, object]:
        return self._entries[source_id]


registry = SourceRegistry()
