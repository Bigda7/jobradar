"""Track source coverage metrics for each ingestion run.

Revision ID: 20260905_0011
Revises: 20260828_0010
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0011"
down_revision: str | None = "20260828_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_runs",
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column("filtered_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column("detail_failure_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column("page_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column("limit_reached", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column("duplicate_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "source_runs",
        sa.Column(
            "normalization_error_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "source_runs",
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("source_runs", "warning_count")
    op.drop_column("source_runs", "normalization_error_count")
    op.drop_column("source_runs", "duplicate_count")
    op.drop_column("source_runs", "limit_reached")
    op.drop_column("source_runs", "page_count")
    op.drop_column("source_runs", "detail_failure_count")
    op.drop_column("source_runs", "filtered_count")
    op.drop_column("source_runs", "candidate_count")
