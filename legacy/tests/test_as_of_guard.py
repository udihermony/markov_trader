"""Acceptance criterion 3: no module can read price data past `as_of`."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DataConfig  # noqa: E402
from data_provider import DataProvider  # noqa: E402
from db import get_connection  # noqa: E402


def seed(conn, ticker: str, days: list[str]):
    conn.executemany(
        "INSERT INTO price_cache (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, 100, 101, 99, 100, 1000)",
        [(ticker, d) for d in days],
    )
    conn.commit()


def test_get_bars_never_returns_future_data(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    seed(conn, "AAA", ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"])
    provider = DataProvider(conn, DataConfig())

    bars = provider.get_bars("AAA", as_of=date(2026, 1, 7))
    assert len(bars) == 3
    assert max(bars.index) <= date(2026, 1, 7)


def test_get_close_respects_as_of(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    seed(conn, "AAA", ["2026-01-05", "2026-01-09"])
    conn.execute("UPDATE price_cache SET close = 999 WHERE date = '2026-01-09'")
    conn.commit()
    provider = DataProvider(conn, DataConfig())
    # as_of between the two bars → must return the earlier close, never 999.
    assert provider.get_close("AAA", date(2026, 1, 7)) == 100.0
