"""Track the listing content used for each match evaluation.

Revision ID: 20260822_0002
Revises: 20260822_0001
Create Date: 2026-08-22 16:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0002"
down_revision: str | None = "20260822_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "match_evaluations",
        sa.Column("listing_content_hash", sa.String(length=64), nullable=True),
    )
    op.execute("UPDATE match_evaluations SET listing_content_hash = ''")
    op.alter_column("match_evaluations", "listing_content_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("match_evaluations", "listing_content_hash")
