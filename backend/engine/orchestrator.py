"""Daily orchestration — one function: run_day(as_of).

Execution timing (both modes): signals are computed on the close of day t and
persisted to `orders` (status='pending'); fills happen at the *open of the
next trading day* plus slippage. Filling at the signal close would be
look-ahead bias.

Idempotent within a day: pending orders are marked executed, duplicate queued
orders for the same (date, ticker, action) are not re-queued, and the daily
equity snapshot upserts on (wallet_id, date).

Every query here is scoped by `wallet_id` — the POC's equivalent queries had
no `run_id` filter at all in several places (e.g. the benchmark baseline scan
over the whole `performance_history` table), which is a latent cross-run
pollution bug once more than one run/wallet shares a database. Wallet-scoping
throughout closes that.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import EquitySnapshot, Fill, Order, Wallet
from backend.engine.graph.compiled import CompiledGraph
from backend.engine.graph.types import NodeResult, PortfolioView
from backend.engine.sandbox import Sandbox, SizingConfig
from backend.sources.price_bars import DataConfig, PriceBarsSource

log = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    def __init__(
        self,
        session: Session,
        wallet_id: int,
        data_cfg: DataConfig,
        sizing: SizingConfig,
        price_bars: PriceBarsSource,
        sandbox: Sandbox,
        graph: CompiledGraph,
        entry_randomizer: Callable[[], bool] | None = None,
    ):
        self.session = session
        self.wallet_id = wallet_id
        self.data_cfg = data_cfg
        self.sizing = sizing
        self.price_bars = price_bars
        self.sandbox = sandbox
        self.graph = graph
        # Lab's luck test only (backend/engine/backtest_runner.py): when set,
        # replaces the trigger's pass/fail with a calibrated coin flip while
        # leaving universe/exits/sizing/slippage untouched. None for every
        # other caller (CLI, wallet runner) — zero behavior change.
        self.entry_randomizer = entry_randomizer
        self.initial_cash = float(
            session.execute(select(Wallet.initial_cash).where(Wallet.id == wallet_id)).scalar_one()
        )

    def _portfolio_view(self) -> PortfolioView:
        return PortfolioView(
            cash=self.sandbox.cash, open_position_count=len(self.sandbox.get_open_positions())
        )

    # ------------------------------------------------------------------ daily
    def run_day(self, as_of: date, quiet: bool = False) -> None:
        # We need bars for as_of before executing pending orders (they fill
        # at as_of's open).
        watchlist = self.graph.candidates(as_of)
        open_tickers = [p.ticker for p in self.sandbox.get_open_positions()]
        pending_tickers = list(
            self.session.execute(
                select(Order.ticker)
                .where(Order.wallet_id == self.wallet_id, Order.status == "pending")
                .distinct()
            ).scalars()
        )
        # Open positions always stay in the loop — otherwise exits never fire.
        universe = list(dict.fromkeys(watchlist + open_tickers + pending_tickers))
        self.price_bars.refresh(universe + [self.data_cfg.benchmark_ticker], as_of)

        # 1. Execute pending orders at today's open.
        self._execute_pending(as_of)

        # 2. Exits first.
        queued_exits = self._evaluate_exits(as_of)

        # 3. Entries second, respecting the cap net of queued exits.
        self._evaluate_entries(as_of, watchlist, queued_exits)

        # 4. Snapshot performance (mark-to-market at as_of closes + SPY benchmark).
        prices = self._closing_prices(as_of)
        self.sandbox.snapshot_performance(as_of, prices, self._benchmark_equity(as_of))

        # 5. Dashboard.
        if not quiet:
            self.render_dashboard(as_of, prices)

    # -------------------------------------------------------------- execution
    def _execute_pending(self, as_of: date) -> None:
        rows = self.session.execute(
            select(Order)
            .where(
                Order.wallet_id == self.wallet_id,
                Order.status == "pending",
                Order.created_date < as_of,
            )
            .order_by(Order.action.desc())  # SELL before BUY: free cash first
        ).scalars().all()
        for row in rows:
            open_price = self.price_bars.get_open(row.ticker, as_of)
            if open_price is None:
                log.warning("no open price for %s on %s; order stays pending", row.ticker, as_of)
                continue
            meta = row.metadata_json or {}
            if row.action == "SELL":
                self.sandbox.execute_sell(row.ticker, open_price, as_of, row.reason, meta)
            else:
                self.sandbox.execute_buy(
                    row.ticker, float(row.cash_amount), open_price, as_of, row.reason, meta
                )
            row.status = "executed"
        self.session.commit()

    def _queue_order(
        self, as_of: date, ticker: str, action: str, result: NodeResult,
        cash_amount: float | None = None,
    ) -> bool:
        dup = self.session.execute(
            select(Order).where(
                Order.wallet_id == self.wallet_id,
                Order.status == "pending",
                Order.created_date == as_of,
                Order.ticker == ticker,
                Order.action == action,
            )
        ).scalar_one_or_none()
        if dup is not None:
            return False  # idempotency: already queued this run-day
        self.session.add(
            Order(
                wallet_id=self.wallet_id, created_date=as_of, ticker=ticker,
                action=action, cash_amount=cash_amount, reason=result.reason,
                metadata_json=result.metadata, status="pending",
            )
        )
        self.session.commit()
        return True

    # ---------------------------------------------------------------- signals
    def _evaluate_exits(self, as_of: date) -> int:
        queued = 0
        portfolio = self._portfolio_view()
        for pos in self.sandbox.get_open_positions():
            result = self.graph.evaluate_exit(pos.ticker, pos, as_of, portfolio)
            if result is not None and self._queue_order(as_of, pos.ticker, "SELL", result):
                queued += 1
        return queued

    def _evaluate_entries(self, as_of: date, watchlist: list[str], queued_exits: int) -> None:
        open_positions = {p.ticker for p in self.sandbox.get_open_positions()}
        pending_buys = set(
            self.session.execute(
                select(Order.ticker).where(
                    Order.wallet_id == self.wallet_id,
                    Order.status == "pending",
                    Order.action == "BUY",
                )
            ).scalars()
        )
        # Cap counts positions net of queued exits plus already-queued buys.
        slots = self.sizing.max_concurrent_positions - (len(open_positions) - queued_exits) - len(pending_buys)
        portfolio = self._portfolio_view()
        for ticker in watchlist:
            if ticker in open_positions or ticker in pending_buys:
                continue
            bars = self.price_bars.get_bars(ticker, as_of)
            if len(bars) < self.data_cfg.min_history_days:
                continue
            if self.entry_randomizer is not None:
                if not self.entry_randomizer():
                    continue
                result = NodeResult(
                    passed=True, reason="luck_test_random_entry",
                    explanation="Random entry (luck test)", missing=[], metadata={},
                )
            else:
                result = self.graph.evaluate_entry(ticker, as_of, portfolio)
                if not result.passed:
                    continue
            if slots <= 0:
                log.info("max_concurrent_positions reached; skipping BUY %s", ticker)
                continue
            cash_amount = self.graph.size(ticker, as_of, portfolio)
            if self._queue_order(as_of, ticker, "BUY", result, cash_amount):
                slots -= 1

    # ------------------------------------------------------------------ marks
    def _closing_prices(self, as_of: date) -> dict[str, float]:
        prices: dict[str, float] = {}
        for pos in self.sandbox.get_open_positions():
            p = self.price_bars.get_close(pos.ticker, as_of)
            if p is not None:
                prices[pos.ticker] = p
        return prices

    def _benchmark_equity(self, as_of: date) -> float | None:
        """Same starting capital fully invested in SPY since the first snapshot."""
        spy = self.data_cfg.benchmark_ticker
        close_now = self.price_bars.get_close(spy, as_of)
        if close_now is None:
            return None
        first = self.session.execute(
            select(func.min(EquitySnapshot.date)).where(EquitySnapshot.wallet_id == self.wallet_id)
        ).scalar_one_or_none()
        if first is None:
            return self.initial_cash  # day one: baseline
        close_start = self.price_bars.get_close(spy, first)
        if not close_start:
            return None
        return self.initial_cash * close_now / close_start

    # -------------------------------------------------------------- dashboard
    def render_dashboard(self, as_of: date, prices: dict[str, float]) -> None:
        cash = self.sandbox.cash
        equity = self.sandbox.get_portfolio_value(prices)
        roi = (equity / self.initial_cash - 1) * 100

        bench = self.session.execute(
            select(EquitySnapshot.benchmark_equity).where(
                EquitySnapshot.wallet_id == self.wallet_id, EquitySnapshot.date == as_of
            )
        ).scalar_one_or_none()
        bench_roi = (float(bench) / self.initial_cash - 1) * 100 if bench else None

        # Max drawdown to date from equity_snapshots.
        rows = self.session.execute(
            select(EquitySnapshot.total_equity)
            .where(EquitySnapshot.wallet_id == self.wallet_id)
            .order_by(EquitySnapshot.date)
        ).scalars().all()
        peak, max_dd = 0.0, 0.0
        for total_equity in rows:
            peak = max(peak, float(total_equity))
            if peak > 0:
                max_dd = max(max_dd, (peak - float(total_equity)) / peak)

        console.rule(f"[bold]Swing Agent — {as_of}")
        pos_table = Table(title="Open Positions")
        for col in ("Ticker", "Shares", "Entry", "Last", "Unreal P&L", "Days held"):
            pos_table.add_column(col, justify="right")
        for pos in self.sandbox.get_open_positions():
            last = prices.get(pos.ticker, pos.entry_price)
            pnl = (last - pos.entry_price) * pos.shares
            days = (as_of - pos.entry_date).days
            pos_table.add_row(pos.ticker, str(pos.shares), f"{pos.entry_price:.2f}",
                              f"{last:.2f}", f"{pnl:+,.2f}", str(days))
        console.print(pos_table)

        console.print(f"Cash: [bold]${cash:,.2f}[/]   Total equity: [bold]${equity:,.2f}[/]   "
                      f"ROI: [bold]{roi:+.2f}%[/]"
                      + (f"   vs SPY: [bold]{roi - bench_roi:+.2f}pp[/]" if bench_roi is not None else "")
                      + f"   Max DD: [bold]{max_dd * 100:.2f}%[/]")

        trades = Table(title="Last 5 Fills")
        for col in ("Date", "Ticker", "Action", "Shares", "Fill", "Reason"):
            trades.add_column(col)
        fills = self.session.execute(
            select(Fill)
            .where(Fill.wallet_id == self.wallet_id)
            .order_by(Fill.id.desc())
            .limit(5)
        ).scalars().all()
        for f in fills:
            trades.add_row(f.timestamp.date().isoformat(), f.ticker, f.action,
                           str(f.shares), f"{float(f.fill_price):.2f}", f.reason)
        console.print(trades)

    # ---------------------------------------------------------------- summary
    def print_backtest_summary(self) -> None:
        from backend.engine.backtest_runner import compute_metrics

        rows = self.session.execute(
            select(EquitySnapshot)
            .where(EquitySnapshot.wallet_id == self.wallet_id)
            .order_by(EquitySnapshot.date)
        ).scalars().all()
        if not rows:
            console.print("[yellow]No performance history recorded.")
            return

        fills = self.session.execute(
            select(Fill).where(Fill.wallet_id == self.wallet_id).order_by(Fill.id)
        ).scalars().all()
        m = compute_metrics(rows, fills, self.initial_cash)

        console.rule("[bold]Backtest Summary")
        console.print(f"Total return:      {m.total_return_pct:+.2f}%")
        if m.benchmark_return_pct is not None:
            console.print(
                f"Benchmark (SPY):   {m.benchmark_return_pct:+.2f}%   "
                f"(excess {m.total_return_pct - m.benchmark_return_pct:+.2f}pp)"
            )
        console.print(f"Max drawdown:      {m.max_drawdown_pct:.2f}%")
        console.print(f"Fills (BUY+SELL):  {m.n_trades}")
        if m.n_closed_trades:
            console.print(f"Hit rate:          {m.hit_rate:.1f}%  ({m.n_closed_trades} closed)")
            console.print(f"Avg holding days:  {m.avg_holding_days_calendar:.1f} (calendar)")
