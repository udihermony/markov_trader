"""Daily OHLCV source backed by yfinance with a Postgres cache.

Prices use yfinance auto_adjust=True: splits and dividends are folded into
OHLC, so backtests are on adjusted prices (documented limitation — historical
raw fills would differ slightly).

The `as_of` guard lives HERE: `get_bars`/`get_close`/`get_open` filter
`date <= as_of` at the query level. This is a query-time filter enforced by
convention and code review, not a database constraint — Postgres has no
mechanism to block a query issued from outside this module, same as the POC.
Nothing outside `sources/` may query `price_bars` directly. This is the hard
look-ahead invariant for both backtest and paper modes.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import Instrument, PriceBar

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataConfig:
    fetch_window_days: int = 90
    min_history_days: int = 25
    refresh_last_bars: int = 1
    retries: int = 2
    backoff_seconds: float = 1.5
    benchmark_ticker: str = "SPY"


class PriceBarsSource:
    def __init__(self, session: Session, cfg: DataConfig):
        self.session = session
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

    def _get_or_create_instrument_id(self, ticker: str) -> int:
        instrument = self.session.execute(
            select(Instrument).where(Instrument.ticker == ticker)
        ).scalar_one_or_none()
        if instrument is None:
            instrument = Instrument(ticker=ticker)
            self.session.add(instrument)
            self.session.flush()
        return instrument.id

    def _get_instrument_id(self, ticker: str) -> int | None:
        return self.session.execute(
            select(Instrument.id).where(Instrument.ticker == ticker)
        ).scalar_one_or_none()

    def _refresh_one(self, ticker: str, start: date, end: date) -> None:
        instrument_id = self._get_or_create_instrument_id(ticker)
        cached = set(
            self.session.execute(
                select(PriceBar.date).where(
                    PriceBar.instrument_id == instrument_id,
                    PriceBar.date >= start,
                    PriceBar.date <= end,
                )
            )
            .scalars()
            .all()
        )
        # Drop the most recent N cached bars so they get re-fetched.
        if self.cfg.refresh_last_bars:
            for d in sorted(cached)[-self.cfg.refresh_last_bars :]:
                cached.discard(d)

        # Missing-business-day heuristic: fetch if any weekday in range is uncached.
        need_fetch = any(
            (start + timedelta(days=i)) not in cached
            for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5
        )
        if not need_fetch:
            return

        df = self._download_with_retry(ticker, start, end)
        if df is None or df.empty:
            return

        for idx, r in df.iterrows():
            bar_date = idx.date() if hasattr(idx, "date") else idx
            stmt = pg_insert(PriceBar).values(
                instrument_id=instrument_id,
                date=bar_date,
                open=float(r["Open"]),
                high=float(r["High"]),
                low=float(r["Low"]),
                close=float(r["Close"]),
                volume=int(r["Volume"]),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )
            self.session.execute(stmt)
        self.session.commit()

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
        instrument_id = self._get_instrument_id(ticker)
        rows = []
        if instrument_id is not None:
            rows = (
                self.session.execute(
                    select(PriceBar)
                    .where(
                        PriceBar.instrument_id == instrument_id,
                        PriceBar.date >= start,
                        PriceBar.date <= as_of,
                    )
                    .order_by(PriceBar.date)
                )
                .scalars()
                .all()
            )
        df = pd.DataFrame(
            [
                {
                    "date": r.date,
                    "open": float(r.open),
                    "high": float(r.high),
                    "low": float(r.low),
                    "close": float(r.close),
                    "volume": int(r.volume),
                }
                for r in rows
            ],
            columns=["date", "open", "high", "low", "close", "volume"],
        )
        return df.set_index("date")

    def get_open(self, ticker: str, on: date) -> float | None:
        instrument_id = self._get_instrument_id(ticker)
        if instrument_id is None:
            return None
        row = self.session.execute(
            select(PriceBar.open).where(
                PriceBar.instrument_id == instrument_id, PriceBar.date == on
            )
        ).scalar_one_or_none()
        return float(row) if row is not None else None

    def get_close(self, ticker: str, as_of: date) -> float | None:
        """Most recent close at or before as_of."""
        instrument_id = self._get_instrument_id(ticker)
        if instrument_id is None:
            return None
        row = self.session.execute(
            select(PriceBar.close)
            .where(PriceBar.instrument_id == instrument_id, PriceBar.date <= as_of)
            .order_by(PriceBar.date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return float(row) if row is not None else None

    def trading_days(self, ticker: str, start: date, end: date) -> list[date]:
        """Trading calendar derived from cached bars of `ticker` (usually SPY)."""
        instrument_id = self._get_instrument_id(ticker)
        if instrument_id is None:
            return []
        rows = (
            self.session.execute(
                select(PriceBar.date)
                .where(
                    PriceBar.instrument_id == instrument_id,
                    PriceBar.date >= start,
                    PriceBar.date <= end,
                )
                .order_by(PriceBar.date)
            )
            .scalars()
            .all()
        )
        return list(rows)
