"""Store matched skills as structured evaluation data.

Revision ID: 20260828_0009
Revises: 20260826_0008
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0009"
down_revision: str | None = "20260826_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILL_NAMES = (
    "React",
    "JavaScript",
    "TypeScript",
    "Python",
    "Django",
    "Django REST Framework",
    "PostgreSQL",
    "REST APIs",
    "Vite",
    "HTML/CSS",
    "Shopify/Liquid",
    "SQLAlchemy",
)


def upgrade() -> None:
    json_type = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()),
        "postgresql",
    )
    op.add_column(
        "match_evaluations",
        sa.Column(
            "matched_skills",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    evaluations = sa.table(
        "match_evaluations",
        sa.column("id", sa.BigInteger()),
        sa.column("reasons", json_type),
        sa.column("matched_skills", json_type),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(evaluations.c.id, evaluations.c.reasons)).mappings()

    for row in rows:
        reasons = row["reasons"] if isinstance(row["reasons"], list) else []
        matched_skills = _matched_skills_from_reasons(reasons)
        connection.execute(
            sa.update(evaluations)
            .where(evaluations.c.id == row["id"])
            .values(matched_skills=matched_skills)
        )


def downgrade() -> None:
    op.drop_column("match_evaluations", "matched_skills")


def _matched_skills_from_reasons(reasons: list[object]) -> list[str]:
    known_skills = set(SKILL_NAMES)
    for reason in reasons:
        if not isinstance(reason, str):
            continue
        _, separator, value = reason.partition(":")
        if not separator:
            continue
        candidates = [part.strip().removesuffix(".") for part in value.split(",")]
        if candidates and all(candidate in known_skills for candidate in candidates):
            return candidates
    return []
