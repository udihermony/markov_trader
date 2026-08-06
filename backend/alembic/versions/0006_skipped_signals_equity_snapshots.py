"""skipped_signals, equity_snapshots — first-class skip table (REVIEW.md #8),
wallet-scoped equity uniqueness (fixes legacy's bare UNIQUE(date) index)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skipped_signals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("stage", sa.String, nullable=False),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=True),
    )

    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("cash", sa.Numeric, nullable=False),
        sa.Column("positions_value", sa.Numeric, nullable=False),
        sa.Column("total_equity", sa.Numeric, nullable=False),
        sa.Column("benchmark_equity", sa.Numeric, nullable=True),
        sa.UniqueConstraint("wallet_id", "date", name="uq_equity_snapshots_wallet_date"),
    )


def downgrade() -> None:
    op.drop_table("equity_snapshots")
    op.drop_table("skipped_signals")
