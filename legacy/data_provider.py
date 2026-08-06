"""Daily OHLCV provider backed by yfinance with a SQLite cache.

Prices use yfinance auto_adjust=True: splits and dividends are folded into
OHLC, so backtests are on adjusted prices (documented limitation — historical
raw fills would differ slightly).

The `as_of` guard lives HERE: `get_bars` filters `date <= as_of` at the query
level. No other module may touch `price_cache` directly. This is the hard
look-ahead invariant for both backtest and paper modes.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, timedelta

import pandas as pd

from config import DataConfig

log = logging.getLogger(__name__)


class DataProvider:
    def __init__(self, conn: sqlite3.Connection, cfg: DataConfig):
        self.conn = conn
        self.cfg = cfg

    # ------------------------------------------------------------------ fetch
    def refresh(self, tickers: list[str], as_of: date) -> None:
        """Ensure the cache covers [as_of - fetch_window_days, as_of] for each
        ticker. Fetches only missing dates; always re-fetches the most recent
        `refresh_last_bars` cached bars (intraday-partial protection).
        Per-ticker failures are retried then skipped — one bad ticker must not
        kill the run."""
        start = as_of - timedelta(days=self.cfg.fetch_window_days)
        for ticker in tickers:
            try:
                self._refresh_one(ticker, start, as_of)
            except Exception as exc:  # noqa: BLE001
                log.warning("data fetch failed for %s after retries: %s — skipping", ticker, exc)

    def _refresh_one(self, ticker: str, start: date, end: date) -> None:
        cached = {
            row["date"]
            for row in self.conn.execute(
                "SELECT date FROM price_cache WHERE ticker = ? AND date >= ? AND date <= ?",
                (ticker, start.isoformat(), end.isoformat()),
            )
        }
        # Drop the most recent N cached bars so they get re-fetched.
        for d in sorted(cached)[-self.cfg.refresh_last_bars or 0:] if self.cfg.refresh_last_bars else []:
            cached.discard(d)

        # Missing-business-day heuristic: fetch if any weekday in range is uncached.
        need_fetch = any(
            (start + timedelta(days=i)).isoformat() not in cached
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
        )
        if not need_fetch:
            return

        df = self._download_with_retry(ticker, start, end)
        if df is None or df.empty:
            return
        rows = [
            (
                ticker,
                idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                float(r["Open"]), float(r["High"]), float(r["Low"]),
                float(r["Close"]), int(r["Volume"]),
            )
            for idx, r in df.iterrows()
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO price_cache (ticker, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def _download_with_retry(self, ticker: str, start: date, end: date) -> pd.DataFrame | None:
        import yfinance as yf

        last_exc: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            try:
                df = yf.download(
                    ticker,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),  # yf end is exclusive
                    auto_adjust=True,
                    progress=False,
                )
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return df
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(self.cfg.backoff_seconds * (attempt + 1))
        raise RuntimeError(f"yfinance download failed for {ticker}") from last_exc

    # ------------------------------------------------------------------- read
    def get_bars(self, ticker: str, as_of: date, lookback_days: int | None = None) -> pd.DataFrame:
        """Public read API. Hard invariant: never returns data past `as_of`."""
        lookback_days = lookback_days or self.cfg.fetch_window_days
        start = as_of - timedelta(days=lookback_days)
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume FROM price_cache "
            "WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
            self.conn,
            params=(ticker, start.isoformat(), as_of.isoformat()),
        )
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.set_index("date")

    def get_open(self, ticker: str, on: date) -> float | None:
        row = self.conn.execute(
            "SELECT open FROM price_cache WHERE ticker = ? AND date = ?",
            (ticker, on.isoformat()),
        ).fetchone()
        return float(row["open"]) if row else None

    def get_close(self, ticker: str, as_of: date) -> float | None:
        """Most recent close at or before as_of."""
        row = self.conn.execute(
            "SELECT close FROM price_cache WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker, as_of.isoformat()),
        ).fetchone()
        return float(row["close"]) if row else None

    def trading_days(self, ticker: str, start: date, end: date) -> list[date]:
        """Trading calendar derived from cached bars of `ticker` (usually SPY)."""
        rows = self.conn.execute(
            "SELECT date FROM price_cache WHERE ticker = ? AND date >= ? AND date <= ? ORDER BY date",
            (ticker, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [date.fromisoformat(r["date"]) for r in rows]
