"""Daily orchestration — one function: run_day(as_of).

Execution timing (both modes): signals are computed on the close of day t and
persisted to `pending_orders`; fills happen at the *open of the next trading
day* plus slippage. Filling at the signal close would be look-ahead bias.

Idempotent within a day: pending orders are marked executed, duplicate queued
orders for the same (date, ticker, action) are not re-queued, and the daily
performance snapshot upserts on date.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from rich.console import Console
from rich.table import Table

from config import Config
from data_provider import DataProvider
from sandbox import Sandbox
from screener import Screener
from strategy import Action, Signal, Strategy

log = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    def __init__(
        self, cfg: Config, provider: DataProvider, screener: Screener,
        sandbox: Sandbox, strategy: Strategy, run_id: str,
    ):
        self.cfg = cfg
        self.provider = provider
        self.screener = screener
        self.sandbox = sandbox
        self.strategy = strategy
        self.run_id = run_id
        self.conn = sandbox.conn

    # ------------------------------------------------------------------ daily
    def run_day(self, as_of: date, quiet: bool = False) -> None:
        # 2/3 first pass: we need bars for as_of before executing pending
        # orders (they fill at as_of's open).
        watchlist = self.screener.get_watchlist(as_of)
        open_tickers = [p.ticker for p in self.sandbox.get_open_positions()]
        pending_tickers = [
            r["ticker"] for r in self.conn.execute(
                "SELECT DISTINCT ticker FROM pending_orders WHERE status = 'pending'"
            )
        ]
        # Open positions always stay in the loop — otherwise exits never fire.
        universe = list(dict.fromkeys(watchlist + open_tickers + pending_tickers))
        self.provider.refresh(universe + [self.cfg.data.benchmark_ticker], as_of)

        # 1. Execute pending orders at today's open.
        self._execute_pending(as_of)

        # 4. Exits first.
        queued_exits = self._evaluate_exits(as_of)

        # 5. Entries second, respecting the cap net of queued exits.
        self._evaluate_entries(as_of, watchlist, queued_exits)

        # 6. Snapshot performance (mark-to-market at as_of closes + SPY benchmark).
        prices = self._closing_prices(as_of)
        self.sandbox.snapshot_performance(as_of, prices, self._benchmark_equity(as_of))

        # 7. Dashboard.
        if not quiet:
            self.render_dashboard(as_of, prices)

    # -------------------------------------------------------------- execution
    def _execute_pending(self, as_of: date) -> None:
        rows = self.conn.execute(
            "SELECT * FROM pending_orders WHERE status = 'pending' AND created_date < ? "
            "ORDER BY action DESC",  # SELL before BUY: free cash first
            (as_of.isoformat(),),
        ).fetchall()
        for row in rows:
            open_price = self.provider.get_open(row["ticker"], as_of)
            if open_price is None:
                log.warning("no open price for %s on %s; order stays pending",
                            row["ticker"], as_of)
                continue
            meta = json.loads(row["signal_metadata_json"] or "{}")
            if row["action"] == "SELL":
                self.sandbox.execute_sell(row["ticker"], open_price, as_of,
                                          row["reason"], meta)
            else:
                self.sandbox.execute_buy(row["ticker"], float(row["cash_amount"]),
                                         open_price, as_of, row["reason"], meta)
            self.conn.execute("UPDATE pending_orders SET status = 'executed' WHERE id = ?",
                              (row["id"],))
        self.conn.commit()

    def _queue_order(self, as_of: date, ticker: str, action: Action,
                     signal: Signal, cash_amount: float | None = None) -> bool:
        dup = self.conn.execute(
            "SELECT 1 FROM pending_orders WHERE status = 'pending' AND created_date = ? "
            "AND ticker = ? AND action = ?",
            (as_of.isoformat(), ticker, action.value),
        ).fetchone()
        if dup:
            return False  # idempotency: already queued this run-day
        self.conn.execute(
            "INSERT INTO pending_orders (run_id, created_date, ticker, action, "
            "cash_amount, reason, signal_metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, as_of.isoformat(), ticker, action.value, cash_amount,
             signal.reason, json.dumps(signal.metadata)),
        )
        self.conn.commit()
        return True

    # ---------------------------------------------------------------- signals
    def _evaluate_exits(self, as_of: date) -> int:
        queued = 0
        for pos in self.sandbox.get_open_positions():
            bars = self.provider.get_bars(pos.ticker, as_of)
            if bars.empty:
                continue
            sig = self.strategy.generate_signal(bars, pos, as_of)
            if sig.action is Action.SELL and self._queue_order(as_of, pos.ticker, Action.SELL, sig):
                queued += 1
        return queued

    def _evaluate_entries(self, as_of: date, watchlist: list[str], queued_exits: int) -> None:
        open_positions = {p.ticker for p in self.sandbox.get_open_positions()}
        pending_buys = {
            r["ticker"] for r in self.conn.execute(
                "SELECT ticker FROM pending_orders WHERE status = 'pending' AND action = 'BUY'"
            )
        }
        # Cap counts positions net of queued exits plus already-queued buys.
        slots = (self.cfg.sizing.max_concurrent_positions
                 - (len(open_positions) - queued_exits) - len(pending_buys))
        for ticker in watchlist:
            if ticker in open_positions or ticker in pending_buys:
                continue
            bars = self.provider.get_bars(ticker, as_of)
            if len(bars) < self.cfg.data.min_history_days:
                continue
            sig = self.strategy.generate_signal(bars, None, as_of)
            if sig.action is not Action.BUY:
                continue
            if slots <= 0:
                log.info("max_concurrent_positions reached; skipping BUY %s", ticker)
                continue
            cash_amount = self.sandbox.cash * self.cfg.sizing.cash_fraction
            if self._queue_order(as_of, ticker, Action.BUY, sig, cash_amount):
                slots -= 1

    # ------------------------------------------------------------------ marks
    def _closing_prices(self, as_of: date) -> dict[str, float]:
        prices: dict[str, float] = {}
        for pos in self.sandbox.get_open_positions():
            p = self.provider.get_close(pos.ticker, as_of)
            if p is not None:
                prices[pos.ticker] = p
        return prices

    def _benchmark_equity(self, as_of: date) -> float | None:
        """Same starting capital fully invested in SPY since the first snapshot."""
        spy = self.cfg.data.benchmark_ticker
        close_now = self.provider.get_close(spy, as_of)
        if close_now is None:
            return None
        first = self.conn.execute(
            "SELECT MIN(date) AS d FROM performance_history"
        ).fetchone()
        if not first or not first["d"]:
            return self.cfg.sizing.initial_cash  # day one: baseline
        close_start = self.provider.get_close(spy, date.fromisoformat(first["d"]))
        if not close_start:
            return None
        return self.cfg.sizing.initial_cash * close_now / close_start

    # -------------------------------------------------------------- dashboard
    def render_dashboard(self, as_of: date, prices: dict[str, float]) -> None:
        cash = self.sandbox.cash
        equity = self.sandbox.get_portfolio_value(prices)
        initial = self.cfg.sizing.initial_cash
        roi = (equity / initial - 1) * 100

        bench = self.conn.execute(
            "SELECT benchmark_equity FROM performance_history WHERE date = ?",
            (as_of.isoformat(),),
        ).fetchone()
        bench_roi = ((bench["benchmark_equity"] / initial - 1) * 100
                     if bench and bench["benchmark_equity"] else None)

        # Max drawdown to date from performance_history.
        peak, max_dd = 0.0, 0.0
        for r in self.conn.execute("SELECT total_equity FROM performance_history ORDER BY date"):
            peak = max(peak, r["total_equity"])
            if peak > 0:
                max_dd = max(max_dd, (peak - r["total_equity"]) / peak)

        console.rule(f"[bold]Swing Agent — {as_of} ({self.cfg.mode})")
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

        trades = Table(title="Last 5 Trades")
        for col in ("Date", "Ticker", "Action", "Shares", "Fill", "Reason"):
            trades.add_column(col)
        for r in self.conn.execute(
            "SELECT timestamp, ticker, action, shares, fill_price, reason FROM trade_log "
            "WHERE action != 'SKIP' ORDER BY id DESC LIMIT 5"
        ):
            trades.add_row(r["timestamp"], r["ticker"], r["action"],
                           str(r["shares"] or ""),
                           f"{r['fill_price']:.2f}" if r["fill_price"] else "",
                           r["reason"])
        console.print(trades)

    # ---------------------------------------------------------------- summary
    def print_backtest_summary(self) -> None:
        initial = self.cfg.sizing.initial_cash
        rows = self.conn.execute(
            "SELECT date, total_equity, benchmark_equity FROM performance_history ORDER BY date"
        ).fetchall()
        if not rows:
            console.print("[yellow]No performance history recorded.")
            return
        final = rows[-1]
        total_ret = (final["total_equity"] / initial - 1) * 100
        bench_ret = ((final["benchmark_equity"] / initial - 1) * 100
                     if final["benchmark_equity"] else None)

        peak, max_dd = 0.0, 0.0
        for r in rows:
            peak = max(peak, r["total_equity"])
            if peak > 0:
                max_dd = max(max_dd, (peak - r["total_equity"]) / peak)

        sells = self.conn.execute(
            "SELECT t_sell.ticker, t_sell.shares, t_sell.fill_price AS exit_p, t_sell.timestamp "
            "FROM trade_log t_sell WHERE t_sell.action = 'SELL' ORDER BY t_sell.id"
        ).fetchall()
        buys = {  # FIFO single-position model: last BUY before the SELL
            (r["ticker"], r["id"]): r for r in self.conn.execute(
                "SELECT * FROM trade_log WHERE action = 'BUY' ORDER BY id"
            )
        }
        n_trades = self.conn.execute(
            "SELECT COUNT(*) AS n FROM trade_log WHERE action IN ('BUY','SELL')"
        ).fetchone()["n"]

        wins = holds = closed = 0
        total_hold = 0
        for s in sells:
            b = self.conn.execute(
                "SELECT fill_price, timestamp FROM trade_log WHERE action='BUY' AND ticker=? "
                "AND timestamp <= ? ORDER BY id DESC LIMIT 1",
                (s["ticker"], s["timestamp"]),
            ).fetchone()
            if b:
                closed += 1
                if s["exit_p"] > b["fill_price"]:
                    wins += 1
                total_hold += (date.fromisoformat(s["timestamp"])
                               - date.fromisoformat(b["timestamp"])).days

        console.rule("[bold]Backtest Summary")
        console.print(f"Total return:      {total_ret:+.2f}%")
        if bench_ret is not None:
            console.print(f"Benchmark (SPY):   {bench_ret:+.2f}%   (excess {total_ret - bench_ret:+.2f}pp)")
        console.print(f"Max drawdown:      {max_dd * 100:.2f}%")
        console.print(f"Fills (BUY+SELL):  {n_trades}")
        if closed:
            console.print(f"Hit rate:          {wins / closed * 100:.1f}%  ({wins}/{closed})")
            console.print(f"Avg holding days:  {total_hold / closed:.1f} (calendar)")
