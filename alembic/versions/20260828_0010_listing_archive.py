"""Track why source listings are archived.

Revision ID: 20260828_0010
Revises: 20260828_0009
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0010"
down_revision: str | None = "20260828_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("archive_reason", sa.String(length=50)))
    op.add_column("listings", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text(
            "UPDATE listings SET archive_reason = 'missing', archived_at = updated_at "
            "WHERE is_active = false"
        )
    )


def downgrade() -> None:
    op.drop_column("listings", "archived_at")
    op.drop_column("listings", "archive_reason")
