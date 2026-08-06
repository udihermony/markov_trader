"""CLI entry point.

  python main.py paper
  python main.py backtest --start YYYY-MM-DD --end YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import logging
import uuid
from datetime import date, timedelta

from config import load_config
from data_provider import DataProvider
from db import get_connection
from orchestrator import Orchestrator
from sandbox import Sandbox
from screener import Screener
from strategy import build_strategy

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def build(mode: str):
    cfg = load_config(mode)
    conn = get_connection(cfg.db_path)
    run_id = str(uuid.uuid4())
    provider = DataProvider(conn, cfg.data)
    screener = Screener(conn, cfg.screener, run_id, mode)
    sandbox = Sandbox(conn, run_id, mode, cfg.sizing, cfg.costs)
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    return cfg, Orchestrator(cfg, provider, screener, sandbox, strategy, run_id)


def cmd_paper(_args) -> None:
    _, orch = build("paper")
    orch.run_day(date.today())


def cmd_backtest(args) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    cfg, orch = build("backtest")

    # Derive the trading calendar from SPY bars in the cache.
    orch.provider.refresh([cfg.data.benchmark_ticker],
                          end)  # ensure SPY covers the range
    # SPY needs bars from before `start` too for warmup/calendar.
    orch.provider._refresh_one(cfg.data.benchmark_ticker,  # noqa: SLF001
                               start - timedelta(days=cfg.data.fetch_window_days), end)
    days = orch.provider.trading_days(cfg.data.benchmark_ticker, start, end)
    if not days:
        raise SystemExit("No trading days found — SPY data unavailable for range.")

    for i, day in enumerate(days):
        orch.run_day(day, quiet=(i < len(days) - 1))
    orch.print_backtest_summary()


def main() -> None:
    parser = argparse.ArgumentParser(prog="swing-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_paper = sub.add_parser("paper", help="single daily run (after market close)")
    p_paper.set_defaults(func=cmd_paper)

    p_bt = sub.add_parser("backtest", help="event-driven loop over historical dates")
    p_bt.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    p_bt.add_argument("--end", required=True, metavar="YYYY-MM-DD")
    p_bt.set_defaults(func=cmd_backtest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
