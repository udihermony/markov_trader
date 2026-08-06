"""Lab — strategies.parent_id (lineage), holdouts (sealed per-user range +
unseal budget), experiments (mandatory hypothesis/expected outcome, actual
outcome, human self-judged prediction_correct, spec snapshot for honest
diffing since strategies are mutable in place).

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "holdouts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("unseals_total", sa.Integer, nullable=False, server_default="3"),
        sa.Column("unseals_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("strategy_id", sa.Integer, sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("hypothesis", sa.Text, nullable=False),
        sa.Column("expected_outcome", sa.Text, nullable=False),
        sa.Column("actual_outcome", sa.Text, nullable=True),
        sa.Column("prediction_correct", sa.Boolean, nullable=True),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("initiated_by", sa.String, nullable=False, server_default="user"),
        sa.Column("status", sa.String, nullable=False, server_default="completed"),
        sa.Column("is_holdout", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("spec_snapshot_json", sa.JSON, nullable=False),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("experiments")
    op.drop_table("holdouts")
    op.drop_constraint("strategies_parent_id_fkey", "strategies", type_="foreignkey")
    op.drop_column("strategies", "parent_id")
