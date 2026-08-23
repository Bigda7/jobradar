"""Add single-user opportunity dispositions.

Revision ID: 20260822_0003
Revises: 20260822_0002
Create Date: 2026-08-22 18:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0003"
down_revision: str | None = "20260822_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opportunity_user_states",
        sa.Column("opportunity_id", sa.BigInteger(), nullable=False),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            name=op.f("fk_opportunity_user_states_opportunity_id_opportunities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("opportunity_id", name=op.f("pk_opportunity_user_states")),
    )
    op.create_index(
        op.f("ix_opportunity_user_states_disposition"),
        "opportunity_user_states",
        ["disposition"],
    )


def downgrade() -> None:
    op.drop_table("opportunity_user_states")
