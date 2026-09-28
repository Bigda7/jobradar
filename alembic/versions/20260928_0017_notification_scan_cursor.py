"""Persist the earliest uncompleted notification scan.

Revision ID: 20260928_0017
Revises: 20260928_0016
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0017"
down_revision: str | None = "20260928_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_scan_cursors",
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("minimum_first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "channel"),
    )


def downgrade() -> None:
    op.drop_table("notification_scan_cursors")
