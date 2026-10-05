"""Separate source update dates from publication dates and repair Djinni history.

Revision ID: 20261005_0020
Revises: 20260928_0019
Create Date: 2026-10-05
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0020"
down_revision: str | None = "20260928_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("listings", "opportunities"):
        op.add_column(
            name, sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
    connection = op.get_bind()
    sources = sa.table(
        "sources", sa.column("id"), sa.column("name"), sa.column("enabled", sa.Boolean())
    )
    listings = sa.table(
        "listings",
        sa.column("id"),
        sa.column("source_id"),
        sa.column("opportunity_id"),
        sa.column("raw_data", sa.JSON()),
        sa.column("normalized_data", sa.JSON()),
        sa.column("content_hash"),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("source_updated_at", sa.DateTime(timezone=True)),
        sa.column("quality_score"),
        sa.column("is_active", sa.Boolean()),
    )
    opportunities = sa.table(
        "opportunities",
        sa.column("id"),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("source_updated_at", sa.DateTime(timezone=True)),
        *(
            sa.column(name)
            for name in (
                "title",
                "company",
                "description",
                "location_text",
                "work_mode",
                "employment_type",
                "contract_type",
                "salary_currency",
                "salary_period",
            )
        ),
        sa.column("salary_min", sa.Numeric(14, 2)),
        sa.column("salary_max", sa.Numeric(14, 2)),
    )
    rows = (
        connection.execute(
            sa.select(listings)
            .join(sources, sources.c.id == listings.c.source_id)
            .where(sources.c.name == "djinni")
        )
        .mappings()
        .all()
    )
    affected: set[int] = set()
    for row in rows:
        payload = dict(row["raw_data"] or {})
        rss = payload.get("rss")
        updated_at: datetime | None = None
        if isinstance(rss, dict):
            try:
                updated_at = parsedate_to_datetime(str(rss.get("pubDate") or ""))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                updated_at = updated_at.astimezone(UTC)
            except (TypeError, ValueError, OverflowError):
                pass
            payload.pop("datePosted", None)
            payload["sourceUpdatedAt"] = updated_at.isoformat() if updated_at else None
        # Keep legacy raw datePosted as evidence, not as an asserted first-publication date.
        normalized = dict(row["normalized_data"]) if row["normalized_data"] is not None else None
        values: dict[str, object] = {
            "published_at": None,
            "source_updated_at": updated_at,
            "raw_data": payload,
        }
        if normalized is not None:
            had_date = normalized.get("published_at") is not None
            normalized["published_at"] = None
            normalized["source_updated_at"] = (
                updated_at.isoformat().replace("+00:00", "Z") if updated_at else None
            )
            hashed = dict(normalized)
            if updated_at is None:
                hashed.pop("source_updated_at")
            serialized = json.dumps(
                {"normalized": hashed, "raw": payload},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            values.update(
                normalized_data=normalized,
                content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                quality_score=row["quality_score"]
                - (100 if had_date and updated_at is None else 0),
            )
        connection.execute(listings.update().where(listings.c.id == row["id"]).values(**values))
        affected.add(row["opportunity_id"])
    for opportunity_id in affected:
        best = (
            connection.execute(
                sa.select(
                    listings.c.published_at,
                    listings.c.source_updated_at,
                    listings.c.normalized_data,
                )
                .join(sources, sources.c.id == listings.c.source_id)
                .where(
                    listings.c.opportunity_id == opportunity_id,
                    listings.c.is_active.is_(True),
                    sources.c.enabled.is_(True),
                    listings.c.normalized_data.is_not(None),
                )
                .order_by(listings.c.quality_score.desc(), listings.c.id.asc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        canonical: dict[str, object] = {
            "published_at": best["published_at"] if best else None,
            "source_updated_at": best["source_updated_at"] if best else None,
        }
        if best:
            for name in (
                "title",
                "company",
                "description",
                "location_text",
                "work_mode",
                "employment_type",
                "contract_type",
                "salary_currency",
                "salary_period",
            ):
                canonical[name] = best["normalized_data"].get(name)
            for name in ("salary_min", "salary_max"):
                value = best["normalized_data"].get(name)
                canonical[name] = Decimal(str(value)) if value is not None else None
        connection.execute(
            opportunities.update().where(opportunities.c.id == opportunity_id).values(**canonical)
        )


def downgrade() -> None:
    # Do not put known update dates back into publication dates during a rollback.
    for name in ("opportunities", "listings"):
        op.drop_column(name, "source_updated_at")
