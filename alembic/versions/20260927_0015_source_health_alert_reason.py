"""Track the active source health alert reason.

Revision ID: 20260927_0015
Revises: 20260925_0014
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0015"
down_revision: str | None = "20260925_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("failure_alert_reason", sa.String(length=30)))
    op.execute(
        "UPDATE sources SET failure_alert_reason = 'failed' WHERE failure_alert_active = true"
    )


def downgrade() -> None:
    op.drop_column("sources", "failure_alert_reason")
