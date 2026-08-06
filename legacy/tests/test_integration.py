"""Offline end-to-end test: multi-day backtest loop over synthetic data.

Covers acceptance criteria 1 (end-to-end run populates trade_log and
performance_history) and 2 (run_day is idempotent within a day).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import load_config  # noqa: E402
from data_provider import DataProvider  # noqa: E402
from db import get_connection  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from sandbox import Sandbox  # noqa: E402
from screener import Screener  # noqa: E402
from strategy import build_strategy  # noqa: E402

ROOT = Path(__file__).parent.parent


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def seed_prices(conn, ticker: str, days: list[date], closes: list[float]):
    conn.executemany(
        "INSERT OR REPLACE INTO price_cache (ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, 1000000)",
        [(ticker, d.isoformat(), c, c * 1.01, c * 0.99, c) for d, c in zip(days, closes)],
    )
    conn.commit()


def build_system(tmp_path):
    cfg = load_config("backtest", ROOT / "config.yaml")
    conn = get_connection(tmp_path / "bt.db")
    run_id = str(uuid.uuid4())
    provider = DataProvider(conn, cfg.data)
    provider.refresh = lambda tickers, as_of: None  # offline: cache is pre-seeded
    screener = Screener(conn, cfg.screener, run_id, "backtest")
    sandbox = Sandbox(conn, run_id, "backtest", cfg.sizing, cfg.costs)
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    return cfg, conn, Orchestrator(cfg, provider, screener, sandbox, strategy, run_id)


def test_backtest_end_to_end(tmp_path):
    cfg, conn, orch = build_system(tmp_path)
    days = weekdays(date(2026, 1, 5), 60)

    # AAA: dips then rallies → upward SMA cross mid-series → BUY, later time stop.
    aaa = [100.0] * 25 + [90.0] * 5 + [70.0 + 3 * i for i in range(30)]
    seed_prices(conn, "AAA", days, aaa)
    # SPY benchmark: gentle rise.
    seed_prices(conn, "SPY", days, [500 + i * 0.5 for i in range(60)])
    # Recorded watchlist for every day (replayed in backtest mode — no finviz).
    conn.executemany(
        "INSERT INTO watchlist_history (run_id, screen_date, ticker, rank) VALUES ('seed', ?, 'AAA', 1)",
        [(d.isoformat(),) for d in days],
    )
    conn.commit()

    for d in days[30:]:
        orch.run_day(d, quiet=True)

    trades = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_log WHERE action IN ('BUY','SELL')"
    ).fetchone()["n"]
    perf = conn.execute("SELECT COUNT(*) AS n FROM performance_history").fetchone()["n"]
    assert trades > 0, "expected at least one fill"
    assert perf == len(days[30:])
    # Benchmark equity recorded and positive.
    row = conn.execute(
        "SELECT benchmark_equity FROM performance_history ORDER BY date DESC LIMIT 1"
    ).fetchone()
    assert row["benchmark_equity"] and row["benchmark_equity"] > 0


def test_run_day_idempotent(tmp_path):
    cfg, conn, orch = build_system(tmp_path)
    days = weekdays(date(2026, 1, 5), 40)
    aaa = [100.0] * 25 + [90.0, 90.0, 130.0] + [131.0] * 12  # cross on day 28
    seed_prices(conn, "AAA", days, aaa)
    seed_prices(conn, "SPY", days, [500.0] * 40)
    conn.executemany(
        "INSERT INTO watchlist_history (run_id, screen_date, ticker, rank) VALUES ('seed', ?, 'AAA', 1)",
        [(d.isoformat(),) for d in days],
    )
    conn.commit()

    signal_day = days[27]
    fill_day = days[28]
    orch.run_day(signal_day, quiet=True)
    orch.run_day(signal_day, quiet=True)  # rerun same day: no duplicate queue
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_orders WHERE status = 'pending'"
    ).fetchone()["n"]
    assert pending == 1

    orch.run_day(fill_day, quiet=True)
    orch.run_day(fill_day, quiet=True)  # rerun: no double execution
    buys = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_log WHERE action = 'BUY'"
    ).fetchone()["n"]
    assert buys == 1
    # Fill happened at day t+1 open, not signal-day close (no look-ahead).
    fill = conn.execute("SELECT timestamp FROM trade_log WHERE action = 'BUY'").fetchone()
    assert fill["timestamp"] == fill_day.isoformat()
