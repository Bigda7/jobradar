"""Track active source coverage alerts.

Revision ID: 20260906_0013
Revises: 20260905_0012
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0013"
down_revision: str | None = "20260905_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "coverage_alert_active",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column("coverage_alert_reason", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "coverage_alert_reason")
    op.drop_column("sources", "coverage_alert_active")
