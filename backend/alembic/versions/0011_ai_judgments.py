"""AI nodes (M10) — `ai_judgments` records every live judgment an AI veto
node makes (DESIGN.md §5.2 point 2: "record, don't replay"), and
`wallets.ai_daily_budget_usd` caps what a wallet is willing to spend on
those judgments per day. Backtests never write here — only a real wallet's
daily run does (backend/sources/ai_judgment.py's LiveAIJudgmentAdapter).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallets",
        sa.Column("ai_daily_budget_usd", sa.Numeric, nullable=True),
    )

    op.create_table(
        "ai_judgments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wallet_id", sa.Integer, sa.ForeignKey("wallets.id"), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("node_type", sa.String, nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("input_context_json", sa.JSON, nullable=False),
        sa.Column("output_json", sa.JSON, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("cost_usd", sa.Numeric, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_judgments")
    op.drop_column("wallets", "ai_daily_budget_usd")
