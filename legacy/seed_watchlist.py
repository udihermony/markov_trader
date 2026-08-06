"""Seed watchlist_history with a fixed S&P 500 watchlist for a date range.

Usage:
    python3 seed_watchlist.py --start 2026-01-05 --end 2026-06-30 --db swing_backtest.db

Inserts a record every Monday (or start date if not Monday) so the backtest
screener fallback has dated rows to replay across the date range.
"""
from __future__ import annotations

import argparse
import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path

TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "GOOGL", "TSLA", "AVGO", "JPM", "BRK-B",
]

def seed(db_path: Path, start: date, end: date) -> None:
    conn = sqlite3.connect(db_path)
    run_id = str(uuid.uuid4())

    # Insert for every Monday (weekly screen cadence) within the range.
    inserted_dates: list[date] = []
    current = start
    while current <= end:
        if current.weekday() == 0 or current == start:  # Monday or first day
            conn.executemany(
                "INSERT OR IGNORE INTO watchlist_history (run_id, screen_date, ticker, rank) "
                "VALUES (?, ?, ?, ?)",
                [(run_id, current.isoformat(), t, i + 1) for i, t in enumerate(TICKERS)],
            )
            inserted_dates.append(current)
            # Advance to next Monday
            days_until_monday = (7 - current.weekday()) % 7 or 7
            current += timedelta(days=days_until_monday)
        else:
            current += timedelta(days=1)

    conn.commit()
    conn.close()
    print(f"Seeded {len(TICKERS)} tickers × {len(inserted_dates)} dates into {db_path}")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Dates: {inserted_dates[0]} … {inserted_dates[-1]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--db", default="swing_backtest.db")
    args = parser.parse_args()
    seed(Path(args.db), date.fromisoformat(args.start), date.fromisoformat(args.end))
