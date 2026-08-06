from __future__ import annotations

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(nullable=True)
    sector: Mapped[str | None] = mapped_column(nullable=True)
    exchange: Mapped[str | None] = mapped_column(nullable=True)


class PriceBar(Base):
    __tablename__ = "price_bars"

    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), primary_key=True
    )
    date: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)


class ScreenResult(Base):
    __tablename__ = "screen_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    screen_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(nullable=False, server_default="finviz")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    spec_version: Mapped[int] = mapped_column(nullable=False, server_default="2")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    initial_cash: Mapped[float] = mapped_column(Numeric, nullable=False)
    cash: Mapped[float] = mapped_column(Numeric, nullable=False)
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default="active")
    is_benchmark: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retired_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("wallet_id", "ticker", name="uq_positions_wallet_ticker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(nullable=False)
    shares: Mapped[int] = mapped_column(nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    entry_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    entry_reason: Mapped[str | None] = mapped_column(nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("action IN ('BUY','SELL')", name="ck_orders_action"),
        CheckConstraint(
            "status IN ('pending','executed','cancelled')", name="ck_orders_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    created_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    cash_amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    reason: Mapped[str] = mapped_column(nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default="pending")
    user_decision: Mapped[str | None] = mapped_column(nullable=True)  # unused in M1 — no approve/skip UI yet


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (CheckConstraint("action IN ('BUY','SELL')", name="ck_fills_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ticker: Mapped[str] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    shares: Mapped[int] = mapped_column(nullable=False)
    fill_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    cost_bps_applied: Mapped[float] = mapped_column(Numeric, nullable=False)
    reason: Mapped[str] = mapped_column(nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SkippedSignal(Base):
    __tablename__ = "skipped_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    ticker: Mapped[str] = mapped_column(nullable=False)
    stage: Mapped[str] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    __table_args__ = (
        UniqueConstraint("wallet_id", "date", name="uq_equity_snapshots_wallet_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    cash: Mapped[float] = mapped_column(Numeric, nullable=False)
    positions_value: Mapped[float] = mapped_column(Numeric, nullable=False)
    total_equity: Mapped[float] = mapped_column(Numeric, nullable=False)
    benchmark_equity: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class Holdout(Base):
    """Sealed once per user (CLAUDE.md rule 7 in spirit — a wallet cannot be
    backdated, and this is the honest-history counterpart: a period neither
    user nor AI may test against except by spending a finite unseal)."""

    __tablename__ = "holdouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    unseals_total: Mapped[int] = mapped_column(nullable=False, server_default="3")
    unseals_used: Mapped[int] = mapped_column(nullable=False, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    actual_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    prediction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    period_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    initiated_by: Mapped[str] = mapped_column(nullable=False, server_default="user")
    status: Mapped[str] = mapped_column(nullable=False, server_default="completed")
    is_holdout: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    spec_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
