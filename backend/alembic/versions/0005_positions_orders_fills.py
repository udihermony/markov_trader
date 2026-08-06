"""positions, orders, fills — wallet-scoped, fixes the POC's global ticker UNIQUE bug

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("shares", sa.Integer, nullable=False),
        sa.Column("avg_entry_price", sa.Numeric, nullable=False),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("entry_reason", sa.String, nullable=True),
        sa.UniqueConstraint("wallet_id", "ticker", name="uq_positions_wallet_ticker"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("created_date", sa.Date, nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("cash_amount", sa.Numeric, nullable=True),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        # Unused in M1 — no approve/skip UI until M5.
        sa.Column("user_decision", sa.String, nullable=True),
        sa.CheckConstraint("action IN ('BUY','SELL')", name="ck_orders_action"),
        sa.CheckConstraint(
            "status IN ('pending','executed','cancelled')", name="ck_orders_status"
        ),
    )

    op.create_table(
        "fills",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("shares", sa.Integer, nullable=False),
        sa.Column("fill_price", sa.Numeric, nullable=False),
        sa.Column("cost_bps_applied", sa.Numeric, nullable=False),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.CheckConstraint("action IN ('BUY','SELL')", name="ck_fills_action"),
    )


def downgrade() -> None:
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("positions")
