"""instruments + price_bars

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String, nullable=False, unique=True),
        sa.Column("name", sa.String, nullable=True),
        sa.Column("sector", sa.String, nullable=True),
        sa.Column("exchange", sa.String, nullable=True),
    )
    op.create_table(
        "price_bars",
        sa.Column(
            "instrument_id",
            sa.Integer,
            sa.ForeignKey("instruments.id"),
            primary_key=True,
        ),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("open", sa.Numeric, nullable=False),
        sa.Column("high", sa.Numeric, nullable=False),
        sa.Column("low", sa.Numeric, nullable=False),
        sa.Column("close", sa.Numeric, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("price_bars")
    op.drop_table("instruments")
