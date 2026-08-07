"""Unattended experiments (M9) — the Postgres job queue DESIGN.md §7
describes ("SELECT ... FOR UPDATE SKIP LOCKED... the POC's single
threading.Lock does not survive"), deferred since M4 because nothing
needed it until a background AI session did. Also adds strategy
provenance (`created_by`) — always wanted (DESIGN.md §3) but nothing
could set it to 'ai' until now.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column("created_by", sa.String, nullable=False, server_default="user"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("progress", sa.JSON, nullable=True),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="ck_jobs_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_column("strategies", "created_by")
