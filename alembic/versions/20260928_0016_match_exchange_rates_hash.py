"""Track exchange-rate snapshots used for match evaluations.

Revision ID: 20260928_0016
Revises: 20260927_0015
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0016"
down_revision: str | None = "20260927_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("match_evaluations", sa.Column("exchange_rates_hash", sa.String(length=64)))


def downgrade() -> None:
    op.drop_column("match_evaluations", "exchange_rates_hash")
