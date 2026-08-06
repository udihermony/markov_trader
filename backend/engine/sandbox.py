"""Virtual paper-trading sandbox, wallet-scoped.

Accounting rules:
- Whole shares only (floor); orders below min_notional are rejected.
- No pyramiding: BUY on a held ticker is skipped and logged.
- Cost model: flat slippage bps against the trader on every fill
  (buys fill higher, sells fill lower). Commission = $0.
- Sells always liquidate the full position.

Rejected signals are logged to `skipped_signals`, not a SKIP row mixed into
the fill log — skipped decisions are as informative as executed ones
(REVIEW.md #8), and this table is genuinely append-only shared source data
independent of `fills`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import EquitySnapshot, Fill, Position as PositionRow, SkippedSignal
from backend.engine.strategy import Position


@dataclass(frozen=True)
class SizingConfig:
    initial_cash: float = 100_000.0
    cash_fraction: float = 0.10
    max_concurrent_positions: int = 8
    min_notional: float = 500.0


@dataclass(frozen=True)
class CostsConfig:
    slippage_bps: float = 5.0


@dataclass
class ExecutionResult:
    executed: bool
    reason: str
    shares: int = 0
    fill_price: float = 0.0
    proceeds: float = 0.0  # cash delta (negative for buys)


def _as_datetime(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


class Sandbox:
    def __init__(
        self,
        session: Session,
        wallet_id: int,
        sizing: SizingConfig,
        costs: CostsConfig,
    ):
        self.session = session
        self.wallet_id = wallet_id
        self.sizing = sizing
        self.costs = costs

    # ---------------------------------------------------------------- account
    @property
    def cash(self) -> float:
        from backend.db.models import Wallet

        return float(
            self.session.execute(
                select(Wallet.cash).where(Wallet.id == self.wallet_id)
            ).scalar_one()
        )

    def _set_cash(self, value: float) -> None:
        from backend.db.models import Wallet

        wallet = self.session.get(Wallet, self.wallet_id)
        wallet.cash = value

    # ----------------------------------------------------------------- trades
    def execute_buy(
        self, ticker: str, cash_amount: float, fill_price: float, as_of: date,
        reason: str, metadata: dict | None = None,
    ) -> ExecutionResult:
        bps = self.costs.slippage_bps
        eff_price = fill_price * (1 + bps / 1e4)  # slippage against the buyer

        if self._get_position(ticker) is not None:
            self._log_skip(ticker, as_of, stage="entry", reason="already_held", metadata=metadata)
            return ExecutionResult(False, "already_held")

        open_count = self.session.execute(
            select(func.count()).select_from(PositionRow).where(PositionRow.wallet_id == self.wallet_id)
        ).scalar_one()
        if open_count >= self.sizing.max_concurrent_positions:
            self._log_skip(ticker, as_of, stage="entry", reason="max_positions", metadata=metadata)
            return ExecutionResult(False, "max_positions")

        cash_amount = min(cash_amount, self.cash)
        shares = int(cash_amount // eff_price)  # whole shares, floored
        notional = shares * eff_price
        if shares <= 0 or notional < self.sizing.min_notional:
            r = "insufficient_cash" if shares <= 0 else "min_notional"
            self._log_skip(ticker, as_of, stage="entry", reason=r, metadata=metadata)
            return ExecutionResult(False, r)

        self._set_cash(self.cash - notional)
        self.session.add(
            PositionRow(
                wallet_id=self.wallet_id, ticker=ticker, shares=shares,
                avg_entry_price=eff_price, entry_date=as_of, entry_reason=reason,
            )
        )
        self._log_fill("BUY", ticker, as_of, shares=shares, fill_price=eff_price,
                        bps=bps, reason=reason, metadata=metadata)
        self.session.commit()
        return ExecutionResult(True, reason, shares, eff_price, -notional)

    def execute_sell(
        self, ticker: str, fill_price: float, as_of: date,
        reason: str, metadata: dict | None = None,
    ) -> ExecutionResult:
        pos = self._get_position(ticker)
        if pos is None:
            self._log_skip(ticker, as_of, stage="exit", reason="no_position", metadata=metadata)
            return ExecutionResult(False, "no_position")

        bps = self.costs.slippage_bps
        eff_price = fill_price * (1 - bps / 1e4)  # slippage against the seller
        proceeds = pos.shares * eff_price

        self._set_cash(self.cash + proceeds)
        self.session.execute(
            PositionRow.__table__.delete().where(
                PositionRow.wallet_id == self.wallet_id, PositionRow.ticker == ticker
            )
        )
        self._log_fill("SELL", ticker, as_of, shares=pos.shares, fill_price=eff_price,
                        bps=bps, reason=reason, metadata=metadata)
        self.session.commit()
        return ExecutionResult(True, reason, pos.shares, eff_price, proceeds)

    # ------------------------------------------------------------------ reads
    def _get_position(self, ticker: str) -> Position | None:
        row = self.session.execute(
            select(PositionRow).where(
                PositionRow.wallet_id == self.wallet_id, PositionRow.ticker == ticker
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return Position(
            ticker=row.ticker, shares=int(row.shares),
            entry_price=float(row.avg_entry_price), entry_date=row.entry_date,
        )

    def get_position(self, ticker: str) -> Position | None:
        return self._get_position(ticker)

    def get_open_positions(self) -> list[Position]:
        rows = self.session.execute(
            select(PositionRow)
            .where(PositionRow.wallet_id == self.wallet_id)
            .order_by(PositionRow.ticker)
        ).scalars().all()
        return [
            Position(
                ticker=r.ticker, shares=int(r.shares),
                entry_price=float(r.avg_entry_price), entry_date=r.entry_date,
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
        existing = self.session.execute(
            select(EquitySnapshot).where(
                EquitySnapshot.wallet_id == self.wallet_id, EquitySnapshot.date == as_of
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.cash = self.cash
            existing.positions_value = positions_value
            existing.total_equity = self.cash + positions_value
            existing.benchmark_equity = benchmark_equity
        else:
            self.session.add(
                EquitySnapshot(
                    wallet_id=self.wallet_id, date=as_of, cash=self.cash,
                    positions_value=positions_value,
                    total_equity=self.cash + positions_value,
                    benchmark_equity=benchmark_equity,
                )
            )
        self.session.commit()

    # ---------------------------------------------------------------- logging
    def _log_fill(
        self, action: str, ticker: str, as_of: date, *,
        shares: int, fill_price: float, bps: float, reason: str,
        metadata: dict | None = None,
    ) -> None:
        self.session.add(
            Fill(
                wallet_id=self.wallet_id, timestamp=_as_datetime(as_of), ticker=ticker,
                action=action, shares=shares, fill_price=fill_price,
                cost_bps_applied=bps, reason=reason, metadata_json=metadata or {},
            )
        )

    def _log_skip(
        self, ticker: str, as_of: date, *, stage: str, reason: str,
        metadata: dict | None = None,
    ) -> None:
        self.session.add(
            SkippedSignal(
                wallet_id=self.wallet_id, date=as_of, ticker=ticker, stage=stage,
                reason=reason, metadata_json=metadata or {},
            )
        )
        self.session.commit()
