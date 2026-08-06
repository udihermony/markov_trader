"""strategies — persisted node-graph specs, wallets.strategy_id gets a real FK

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("spec_json", sa.JSON, nullable=False),
        sa.Column("spec_version", sa.Integer, nullable=False, server_default="2"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_foreign_key(
        "fk_wallets_strategy_id_strategies", "wallets", "strategies", ["strategy_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_wallets_strategy_id_strategies", "wallets", type_="foreignkey")
    op.drop_table("strategies")
