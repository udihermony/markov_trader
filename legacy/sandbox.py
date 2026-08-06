"""Virtual paper-trading sandbox backed by SQLite.

Accounting rules (configurable in config.yaml):
- Whole shares only (floor); orders below min_notional are rejected.
- No pyramiding: BUY on a held ticker is skipped and logged.
- Cost model: flat slippage bps against the trader on every fill
  (buys fill higher, sells fill lower). Commission = $0.
- Sells always liquidate the full position.

Skipped/rejected signals are logged to trade_log with action='SKIP' —
skipped decisions are as informative as executed ones.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from config import CostsConfig, SizingConfig
from strategy import Position


@dataclass
class ExecutionResult:
    executed: bool
    reason: str
    shares: int = 0
    fill_price: float = 0.0
    proceeds: float = 0.0  # cash delta (negative for buys)


class Sandbox:
    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        mode: str,
        sizing: SizingConfig,
        costs: CostsConfig,
    ):
        self.conn = conn
        self.run_id = run_id
        self.mode = mode
        self.sizing = sizing
        self.costs = costs
        self._ensure_account()

    # ---------------------------------------------------------------- account
    def _ensure_account(self) -> None:
        row = self.conn.execute("SELECT id FROM account LIMIT 1").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO account (run_id, mode, cash) VALUES (?, ?, ?)",
                (self.run_id, self.mode, self.sizing.initial_cash),
            )
            self.conn.commit()

    @property
    def cash(self) -> float:
        return float(self.conn.execute("SELECT cash FROM account LIMIT 1").fetchone()["cash"])

    def _set_cash(self, value: float) -> None:
        self.conn.execute("UPDATE account SET cash = ?", (value,))

    # ----------------------------------------------------------------- trades
    def execute_buy(
        self, ticker: str, cash_amount: float, fill_price: float, as_of: date,
        reason: str, metadata: dict | None = None,
    ) -> ExecutionResult:
        bps = self.costs.slippage_bps
        eff_price = fill_price * (1 + bps / 1e4)  # slippage against the buyer

        if self._get_position(ticker) is not None:
            self._log("SKIP", ticker, as_of, reason="already_held", metadata=metadata)
            return ExecutionResult(False, "already_held")

        open_count = self.conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
        if open_count >= self.sizing.max_concurrent_positions:
            self._log("SKIP", ticker, as_of, reason="max_positions", metadata=metadata)
            return ExecutionResult(False, "max_positions")

        cash_amount = min(cash_amount, self.cash)
        shares = int(cash_amount // eff_price)  # whole shares, floored
        notional = shares * eff_price
        if shares <= 0 or notional < self.sizing.min_notional:
            r = "insufficient_cash" if shares <= 0 else "min_notional"
            self._log("SKIP", ticker, as_of, reason=r, metadata=metadata)
            return ExecutionResult(False, r)

        self._set_cash(self.cash - notional)
        self.conn.execute(
            "INSERT INTO positions (run_id, ticker, shares, avg_entry_price, entry_date, entry_signal) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, ticker, shares, eff_price, as_of.isoformat(), reason),
        )
        self._log("BUY", ticker, as_of, shares=shares, fill_price=eff_price,
                  bps=bps, reason=reason, metadata=metadata)
        self.conn.commit()
        return ExecutionResult(True, reason, shares, eff_price, -notional)

    def execute_sell(
        self, ticker: str, fill_price: float, as_of: date,
        reason: str, metadata: dict | None = None,
    ) -> ExecutionResult:
        pos = self._get_position(ticker)
        if pos is None:
            self._log("SKIP", ticker, as_of, reason="no_position", metadata=metadata)
            return ExecutionResult(False, "no_position")

        bps = self.costs.slippage_bps
        eff_price = fill_price * (1 - bps / 1e4)  # slippage against the seller
        proceeds = pos.shares * eff_price

        self._set_cash(self.cash + proceeds)
        self.conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
        self._log("SELL", ticker, as_of, shares=pos.shares, fill_price=eff_price,
                  bps=bps, reason=reason, metadata=metadata)
        self.conn.commit()
        return ExecutionResult(True, reason, pos.shares, eff_price, proceeds)

    # ------------------------------------------------------------------ reads
    def _get_position(self, ticker: str) -> Position | None:
        row = self.conn.execute(
            "SELECT ticker, shares, avg_entry_price, entry_date FROM positions WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if row is None:
            return None
        return Position(
            ticker=row["ticker"], shares=int(row["shares"]),
            entry_price=float(row["avg_entry_price"]),
            entry_date=date.fromisoformat(row["entry_date"]),
        )

    def get_position(self, ticker: str) -> Position | None:
        return self._get_position(ticker)

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT ticker, shares, avg_entry_price, entry_date FROM positions ORDER BY ticker"
        ).fetchall()
        return [
            Position(
                ticker=r["ticker"], shares=int(r["shares"]),
                entry_price=float(r["avg_entry_price"]),
                entry_date=date.fromisoformat(r["entry_date"]),
            )
            for r in rows
        ]

    def get_portfolio_value(self, prices: dict[str, float]) -> float:
        value = self.cash
        for pos in self.get_open_positions():
            price = prices.get(pos.ticker)
            value += pos.shares * (price if price is not None else pos.entry_price)
        return value

    # -------------------------------------------------------------- snapshots
    def snapshot_performance(
        self, as_of: date, prices: dict[str, float], benchmark_equity: float | None
    ) -> None:
        positions_value = self.get_portfolio_value(prices) - self.cash
        self.conn.execute(
            "INSERT OR REPLACE INTO performance_history "
            "(run_id, date, cash, positions_value, total_equity, benchmark_equity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.run_id, as_of.isoformat(), self.cash, positions_value,
             self.cash + positions_value, benchmark_equity),
        )
        self.conn.commit()

    # ---------------------------------------------------------------- logging
    def _log(
        self, action: str, ticker: str, as_of: date, *,
        shares: int | None = None, fill_price: float | None = None,
        bps: float | None = None, reason: str, metadata: dict | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO trade_log (run_id, mode, timestamp, ticker, action, shares, "
            "fill_price, cost_bps_applied, reason, signal_metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, self.mode, as_of.isoformat(), ticker, action, shares,
             fill_price, bps, reason, json.dumps(metadata or {})),
        )
