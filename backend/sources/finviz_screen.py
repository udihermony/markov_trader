"""Stock discovery via the Finviz screener (finvizfinance).

BACKTEST LIMITATION (also in README): Finviz cannot be queried historically.
In backtest mode we either (a) replay rows recorded in `screen_results` for
the requested date, or (b) fall back to a watchlist frozen at backtest
start — which introduces selection/look-ahead bias; results are indicative
only. A logged warning makes this explicit.

Finviz is a scraper and will break: calls are wrapped in retry-with-backoff
and a hard timeout. On failure in paper mode we fall back to the most recent
persisted watchlist and log a warning.

`screen_results` is shared source data (DESIGN.md §6) — no wallet/run
scoping, unlike the POC's per-run `watchlist_history`.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import ScreenResult
from backend.sources.registry import AlignmentPolicy, SourceSpec, TrustClass, registry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenerConfig:
    filters: dict = field(
        default_factory=lambda: {
            "Index": "S&P 500",
            "Price": "Over $20",
            "Average Volume": "Over 500K",
        }
    )
    top_n: int = 10
    retries: int = 3
    backoff_seconds: float = 2.0
    timeout_seconds: float = 30.0


class FinvizScreenSource:
    def __init__(self, session: Session, cfg: ScreenerConfig, mode: str):
        self.session = session
        self.cfg = cfg
        self.mode = mode
        self._frozen: list[str] | None = None  # backtest fallback watchlist

    # ------------------------------------------------------------------ public
    def get_watchlist(self, as_of: date) -> list[str]:
        if self.mode == "backtest":
            return self._backtest_watchlist(as_of)
        return self._paper_watchlist(as_of)

    # ---------------------------------------------------------------- backtest
    def _backtest_watchlist(self, as_of: date) -> list[str]:
        recorded = self._load_recorded_asof(as_of)
        if recorded:
            return recorded
        if self._frozen is None:
            log.warning(
                "No recorded screen on or before %s — freezing today's Finviz watchlist for "
                "the whole backtest. This introduces selection/look-ahead bias; results are "
                "indicative only.", as_of,
            )
            try:
                self._frozen = self._run_finviz()
            except Exception as exc:  # noqa: BLE001
                log.warning("Finviz failed (%s); falling back to last persisted watchlist", exc)
                self._frozen = self._load_latest_recorded() or []
        return self._frozen

    # ------------------------------------------------------------------- paper
    def _paper_watchlist(self, as_of: date) -> list[str]:
        try:
            tickers = self._run_finviz()
        except Exception as exc:  # noqa: BLE001
            log.warning("Finviz screen failed (%s); using most recent persisted watchlist", exc)
            return self._load_latest_recorded() or []
        self._persist(as_of, tickers)
        return tickers

    # ---------------------------------------------------------------- internals
    def _run_finviz(self) -> list[str]:
        last_exc: Exception | None = None
        for attempt in range(self.cfg.retries):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(self._finviz_call)
                    return fut.result(timeout=self.cfg.timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(self.cfg.backoff_seconds * (attempt + 1))
        raise RuntimeError("finviz screen failed after retries") from last_exc

    def _finviz_call(self) -> list[str]:
        from finvizfinance.screener.overview import Overview

        ov = Overview()
        ov.set_filter(filters_dict=dict(self.cfg.filters))
        df = ov.screener_view(order="Market Cap.", ascend=False, verbose=0)
        if df is None or df.empty:
            raise RuntimeError("finviz returned no rows")
        return [str(t) for t in df["Ticker"].head(self.cfg.top_n)]

    def _persist(self, as_of: date, tickers: list[str]) -> None:
        for i, t in enumerate(tickers):
            self.session.add(
                ScreenResult(screen_date=as_of, ticker=t, rank=i + 1, source="finviz")
            )
        self.session.commit()

    def _load_recorded_asof(self, as_of: date) -> list[str]:
        """Most recent watchlist on or before as_of — correct for weekly-cadence screens."""
        latest = self.session.execute(
            select(ScreenResult.screen_date)
            .where(ScreenResult.screen_date <= as_of)
            .order_by(ScreenResult.screen_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        rows = self.session.execute(
            select(ScreenResult.ticker)
            .where(ScreenResult.screen_date == latest)
            .order_by(ScreenResult.rank)
        ).scalars().all()
        return list(rows)[: self.cfg.top_n]

    def _load_latest_recorded(self) -> list[str]:
        latest = self.session.execute(
            select(ScreenResult.screen_date).order_by(ScreenResult.screen_date.desc()).limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        rows = self.session.execute(
            select(ScreenResult.ticker)
            .where(ScreenResult.screen_date == latest)
            .order_by(ScreenResult.rank)
        ).scalars().all()
        return list(rows)[: self.cfg.top_n]


# --------------------------------------------------------------- M2: registry
FINVIZ_SCREEN_SPEC = SourceSpec(
    id="finviz_screen",
    features={},  # not a feature-expression source (no px/x/pm/news/fund-style namespace in
                  # DESIGN.md §4.1) — it's a universe-defining source consumed by `universe`
                  # nodes in M3, not `trigger`/`confirm` expressions. Registered anyway so (a)
                  # the registry/conformance machinery is proven against a structurally
                  # different adapter shape (date+ticker+rank screen results, not
                  # ticker+date-keyed OHLCV), and (b) CLAUDE.md's "sources/ is the only data
                  # chokepoint" applies to every source, not just expression-bearing ones.
    trust_class=TrustClass.RECONSTRUCTABLE,  # watchlist replay depends on recorded history,
                                              # with an explicit look-ahead caveat on the
                                              # frozen-fallback path — weaker than price_bars.
    native_frequency="daily",
    alignment=AlignmentPolicy(native_frequency="daily"),
    coverage_note="Backtest mode replays recorded screen_results; falls back to a frozen "
                  "live screen when nothing is recorded on/before as_of (selection bias — "
                  "see module docstring).",
)


class FinvizScreenAdapter:
    """Doesn't implement `get_series` (it has no features) — this satisfies
    a looser structural need for the registry/conformance harness rather
    than the full `SourceAdapter` Protocol used by expression evaluation."""

    spec = FINVIZ_SCREEN_SPEC

    def __init__(self, screener: FinvizScreenSource):
        self._screener = screener

    def get_watchlist_as_of(self, as_of: date) -> list[str]:
        return self._screener.get_watchlist(as_of)


def register_finviz_screen_source(adapter: FinvizScreenAdapter) -> None:
    registry.register(adapter)
