"""Add the persistent listing detail cache timestamp.

Revision ID: 20260823_0007
Revises: 20260822_0006
Create Date: 2026-08-23 17:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0007"
down_revision: str | None = "20260822_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("detail_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE listings
            SET detail_fetched_at = last_seen_at
            WHERE source_id IN (
                SELECT id
                FROM sources
                WHERE name IN (
                    'workua',
                    'jobs_cz',
                    'prace_cz',
                    'startupjobs_cz',
                    'freelance_cz'
                )
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("listings", "detail_fetched_at")
