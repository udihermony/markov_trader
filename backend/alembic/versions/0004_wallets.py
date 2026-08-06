"""wallets

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        # No FK — the strategies table doesn't exist until M3.
        sa.Column("strategy_id", sa.Integer, nullable=True),
        sa.Column("initial_cash", sa.Numeric, nullable=False),
        sa.Column("cash", sa.Numeric, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("is_benchmark", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("wallets")
