"""Persist Telegram messages and schedule retries without an attempt limit.

Revision ID: 20260928_0019
Revises: 20260928_0018
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0019"
down_revision: str | None = "20260928_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_deliveries",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("message_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("source_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_deliveries", "source_url")
    op.drop_column("notification_deliveries", "message_text")
    op.drop_column("notification_deliveries", "next_attempt_at")
