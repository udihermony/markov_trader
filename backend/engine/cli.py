"""CLI entry point.

  swing-agent paper
  swing-agent backtest --start YYYY-MM-DD --end YYYY-MM-DD

Strategy/sizing parameters are CLI flags with in-code defaults — there is no
config.yaml in v2 (CLAUDE.md rule 8: configuration is not mutable global
state). Defaults are fast=10/slow=20, the SmaCrossover class's own defaults,
deliberately NOT the legacy config.yaml value of fast=19/slow=20 — REVIEW.md
flags 19/20 as a stale, noisy value that was never an intentional choice.
"""
from __future__ import annotations

import argparse
import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import User, Wallet
from backend.db.session import get_session
import backend.engine.graph.nodes  # noqa: F401  registers the node type library
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.spec import NodeSpec, SourceRef, StrategySpec
from backend.engine.orchestrator import Orchestrator
from backend.engine.sandbox import CostsConfig, Sandbox, SizingConfig
from backend.sources.finviz_screen import FinvizScreenAdapter, FinvizScreenSource, ScreenerConfig
from backend.sources.price_bars import DataConfig, PriceBarsFeatureAdapter, PriceBarsSource
from backend.sources.registry import SourceRegistry

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CLI_SYSTEM_USER_EMAIL = "cli@localhost"


def _get_or_create_system_user(session: Session) -> User:
    user = session.execute(
        select(User).where(User.email == CLI_SYSTEM_USER_EMAIL)
    ).scalar_one_or_none()
    if user is None:
        user = User(email=CLI_SYSTEM_USER_EMAIL, password_hash="!cli-user-no-login!")
        session.add(user)
        session.flush()
    return user


def build(session: Session, mode: str, args: argparse.Namespace) -> Orchestrator:
    user = _get_or_create_system_user(session)

    sizing = SizingConfig(
        initial_cash=args.initial_cash,
        cash_fraction=args.cash_fraction,
        max_concurrent_positions=args.max_concurrent_positions,
        min_notional=args.min_notional,
    )
    costs = CostsConfig(slippage_bps=args.slippage_bps)
    data_cfg = DataConfig(min_history_days=args.min_history_days)
    screener_cfg = ScreenerConfig(top_n=args.top_n)

    wallet = Wallet(
        user_id=user.id,
        name=f"cli-{mode}-{uuid.uuid4().hex[:8]}",
        strategy_id=None,
        initial_cash=sizing.initial_cash,
        cash=sizing.initial_cash,
        start_date=date.today(),
        status="active",
        is_benchmark=False,
    )
    session.add(wallet)
    session.flush()

    price_bars = PriceBarsSource(session, data_cfg)
    screener = FinvizScreenSource(session, screener_cfg, mode)
    sandbox = Sandbox(session, wallet.id, sizing, costs)

    # One fresh registry per CLI invocation — no global registration
    # pollution across runs, matching M2's per-test pattern.
    source_registry = SourceRegistry()
    source_registry.register(PriceBarsFeatureAdapter(price_bars))
    source_registry.register(FinvizScreenAdapter(screener))

    fast_expr = f"sma(px.close, {args.fast})"
    slow_expr = f"sma(px.close, {args.slow})"
    spec = StrategySpec(
        name="sma-crossover-cli",
        sources=[SourceRef(id="px", type="price_bars")],
        nodes=[
            NodeSpec(id="u1", kind="universe", type="finviz_screen", params={}),
            NodeSpec(id="t1", kind="trigger", type="cross",
                     params={"a": fast_expr, "b": slow_expr, "direction": "up"}),
            NodeSpec(id="x1", kind="exit", type="cross",
                     params={"a": fast_expr, "b": slow_expr, "direction": "down"}),
            NodeSpec(id="x2", kind="exit", type="time_stop",
                     params={"max_hold_days": args.max_hold_days, "calendar_feature": "px.close"}),
            NodeSpec(id="s1", kind="size", type="fixed_fraction", params={"fraction": args.cash_fraction}),
        ],
        edges=[["u1", "t1"]],
    )
    graph = CompiledGraph(spec, source_registry)

    return Orchestrator(session, wallet.id, data_cfg, sizing, price_bars, sandbox, graph)


def cmd_paper(args: argparse.Namespace) -> None:
    with get_session() as session:
        orch = build(session, "paper", args)
        orch.run_day(date.today())


def cmd_backtest(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    with get_session() as session:
        orch = build(session, "backtest", args)

        # Derive the trading calendar from SPY bars in the cache.
        benchmark = orch.data_cfg.benchmark_ticker
        orch.price_bars.refresh([benchmark], end)  # ensure SPY covers the range
        # SPY needs bars from before `start` too for warmup/calendar.
        orch.price_bars._refresh_one(  # noqa: SLF001
            benchmark, start - timedelta(days=orch.data_cfg.fetch_window_days), end
        )
        days = orch.price_bars.trading_days(benchmark, start, end)
        if not days:
            raise SystemExit("No trading days found — SPY data unavailable for range.")

        for i, day in enumerate(days):
            orch.run_day(day, quiet=(i < len(days) - 1))
        orch.print_backtest_summary()


def main() -> None:
    parser = argparse.ArgumentParser(prog="swing-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_strategy_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--fast", type=int, default=10, help="fast SMA period (default: 10)")
        p.add_argument("--slow", type=int, default=20, help="slow SMA period (default: 20)")
        p.add_argument("--max-hold-days", type=int, default=5)
        p.add_argument("--initial-cash", type=float, default=100_000.0)
        p.add_argument("--cash-fraction", type=float, default=0.10)
        p.add_argument("--max-concurrent-positions", type=int, default=8)
        p.add_argument("--min-notional", type=float, default=500.0)
        p.add_argument("--slippage-bps", type=float, default=5.0)
        p.add_argument("--top-n", type=int, default=10)
        p.add_argument("--min-history-days", type=int, default=25)

    p_paper = sub.add_parser("paper", help="single daily run (after market close)")
    add_strategy_flags(p_paper)
    p_paper.set_defaults(func=cmd_paper)

    p_bt = sub.add_parser("backtest", help="event-driven loop over historical dates")
    p_bt.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    p_bt.add_argument("--end", required=True, metavar="YYYY-MM-DD")
    add_strategy_flags(p_bt)
    p_bt.set_defaults(func=cmd_backtest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
