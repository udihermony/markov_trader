"""screen_results — shared source data, no wallet/run scoping (DESIGN.md §6)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screen_results",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("screen_date", sa.Date, nullable=False),
        sa.Column("ticker", sa.String, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("source", sa.String, nullable=False, server_default="finviz"),
    )
    op.create_index("ix_screen_results_screen_date", "screen_results", ["screen_date"])


def downgrade() -> None:
    op.drop_index("ix_screen_results_screen_date", table_name="screen_results")
    op.drop_table("screen_results")
