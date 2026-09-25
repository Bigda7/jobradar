"""Add trigram indexes for opportunity search.

Revision ID: 20260925_0014
Revises: 20260906_0013
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260925_0014"
down_revision: str | None = "20260906_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_opportunities_title_trgm",
        "opportunities",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_opportunities_company_trgm",
        "opportunities",
        ["company"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"company": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_opportunities_description_trgm",
        "opportunities",
        ["description"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_description_trgm", table_name="opportunities")
    op.drop_index("ix_opportunities_company_trgm", table_name="opportunities")
    op.drop_index("ix_opportunities_title_trgm", table_name="opportunities")
