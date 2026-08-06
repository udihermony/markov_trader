"""Stock discovery via the Finviz screener (finvizfinance).

BACKTEST LIMITATION (also in README): Finviz cannot be queried historically.
In backtest mode we either (a) replay rows recorded in `watchlist_history`
for the requested date, or (b) fall back to a watchlist frozen at backtest
start — which introduces selection/look-ahead bias; results are indicative
only. A logged warning makes this explicit.

Finviz is a scraper and will break: calls are wrapped in retry-with-backoff
and a hard timeout. On failure in paper mode we fall back to the most recent
persisted watchlist and log a warning.
"""
from __future__ import annotations

import concurrent.futures
import logging
import sqlite3
import time
from datetime import date

from config import ScreenerConfig

log = logging.getLogger(__name__)


class Screener:
    def __init__(self, conn: sqlite3.Connection, cfg: ScreenerConfig, run_id: str, mode: str):
        self.conn = conn
        self.cfg = cfg
        self.run_id = run_id
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
        self.conn.executemany(
            "INSERT INTO watchlist_history (run_id, screen_date, ticker, rank) VALUES (?, ?, ?, ?)",
            [(self.run_id, as_of.isoformat(), t, i + 1) for i, t in enumerate(tickers)],
        )
        self.conn.commit()

    def _load_recorded_asof(self, as_of: date) -> list[str]:
        """Most recent watchlist on or before as_of — correct for weekly-cadence screens."""
        row = self.conn.execute(
            "SELECT MAX(screen_date) AS d FROM watchlist_history WHERE screen_date <= ?",
            (as_of.isoformat(),),
        ).fetchone()
        if not row or not row["d"]:
            return []
        rows = self.conn.execute(
            "SELECT ticker FROM watchlist_history WHERE screen_date = ? ORDER BY rank",
            (row["d"],),
        ).fetchall()
        return [r["ticker"] for r in rows][: self.cfg.top_n]

    def _load_latest_recorded(self) -> list[str]:
        row = self.conn.execute("SELECT MAX(screen_date) AS d FROM watchlist_history").fetchone()
        if not row or not row["d"]:
            return []
        rows = self.conn.execute(
            "SELECT ticker FROM watchlist_history WHERE screen_date = ? ORDER BY rank",
            (row["d"],),
        ).fetchall()
        return [r["ticker"] for r in rows][: self.cfg.top_n]
