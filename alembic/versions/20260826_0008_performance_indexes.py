"""Add indexes for API and worker query paths.

Revision ID: 20260826_0008
Revises: 20260823_0007
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260826_0008"
down_revision: str | None = "20260823_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_opportunities_work_mode_published",
        "opportunities",
        ["work_mode", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_listings_source_active",
        "listings",
        ["source_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_listings_opportunity_active",
        "listings",
        ["opportunity_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_match_evaluations_profile_rules_score",
        "match_evaluations",
        ["profile_id", "rules_version", "score"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_match_evaluations_profile_rules_score",
        table_name="match_evaluations",
    )
    op.drop_index("ix_listings_opportunity_active", table_name="listings")
    op.drop_index("ix_listings_source_active", table_name="listings")
    op.drop_index("ix_opportunities_work_mode_published", table_name="opportunities")
