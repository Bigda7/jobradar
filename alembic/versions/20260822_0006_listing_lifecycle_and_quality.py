"""Add listing lifecycle audit data and canonical quality snapshots.

Revision ID: 20260822_0006
Revises: 20260822_0005
Create Date: 2026-08-22 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0006"
down_revision: str | None = "20260822_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_runs",
        sa.Column(
            "deactivated_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "listings",
        sa.Column(
            "normalized_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "listings",
        sa.Column(
            "quality_score",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE listings AS listing
            SET normalized_data = jsonb_build_object(
                    'kind', opportunity.kind,
                    'title', opportunity.title,
                    'company', opportunity.company,
                    'description', opportunity.description,
                    'location_text', opportunity.location_text,
                    'work_mode', opportunity.work_mode,
                    'employment_type', opportunity.employment_type,
                    'contract_type', opportunity.contract_type,
                    'salary_min', opportunity.salary_min,
                    'salary_max', opportunity.salary_max,
                    'salary_currency', opportunity.salary_currency,
                    'salary_period', opportunity.salary_period,
                    'published_at', opportunity.published_at
                ),
                quality_score = LEAST(
                    LENGTH(
                        TRIM(
                            REGEXP_REPLACE(
                                COALESCE(opportunity.description, ''),
                                '\\s+',
                                ' ',
                                'g'
                            )
                        )
                    ),
                    20000
                )
                + CASE
                    WHEN opportunity.salary_min IS NOT NULL
                      OR opportunity.salary_max IS NOT NULL THEN 2000
                    ELSE 0
                  END
                + CASE WHEN opportunity.company IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.location_text IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.employment_type IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.contract_type IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.salary_currency IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.salary_period IS NOT NULL THEN 100 ELSE 0 END
                + CASE WHEN opportunity.published_at IS NOT NULL THEN 100 ELSE 0 END
            FROM opportunities AS opportunity
            WHERE listing.opportunity_id = opportunity.id
            """
        )
    )
    op.alter_column("source_runs", "deactivated_count", server_default=None)
    op.alter_column("listings", "quality_score", server_default=None)


def downgrade() -> None:
    op.drop_column("listings", "quality_score")
    op.drop_column("listings", "normalized_data")
    op.drop_column("source_runs", "deactivated_count")
