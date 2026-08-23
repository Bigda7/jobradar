from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, OpportunityUserState
from jobradar.domain.enums import (
    OpportunityDisposition,
    OpportunityKind,
    OpportunityStatus,
)
from jobradar.ingestion.service import IngestionService
from jobradar.opportunities.expiration import StaleExpirationService
from jobradar.sources.mock import MockSource


class EmploymentExpirationSource(MockSource):
    name = "employment_expiration"
    display_name = "Employment Expiration"


class FreelanceExpirationSource(MockSource):
    name = "freelance_expiration"
    display_name = "Freelance Expiration"
    opportunity_kind = OpportunityKind.FREELANCE_PROJECT


class AlternateEmploymentSource(MockSource):
    name = "alternate_employment_expiration"
    display_name = "Alternate Employment Expiration"


def _listing(
    identifier: str,
    title: str,
    company: str,
    published_at: datetime | None,
    *,
    description: str = "Build React and Django applications.",
) -> dict[str, object]:
    return {
        "id": identifier,
        "url": f"https://expiration.test/jobs/{identifier}",
        "title": title,
        "company": company,
        "description": description,
        "location": "Remote",
        "work_mode": "remote",
        "employment_type": "full_time",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "published_at": published_at.isoformat() if published_at is not None else None,
    }


@pytest.mark.asyncio
async def test_expiration_uses_kind_specific_age_and_protects_favorites(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    employment = (
        _listing("employment-old", "Old Employment", "Old Corp", now - timedelta(days=30)),
        _listing(
            "employment-fresh",
            "Fresh Employment",
            "Fresh Corp",
            now - timedelta(days=29, hours=23),
        ),
        _listing(
            "employment-favorite",
            "Favorite Employment",
            "Favorite Corp",
            now - timedelta(days=90),
        ),
        _listing("employment-fallback", "Fallback Employment", "Fallback Corp", None),
    )
    freelance = (
        _listing("freelance-old", "Old Freelance", "Old Client", now - timedelta(days=7)),
        _listing(
            "freelance-fresh",
            "Fresh Freelance",
            "Fresh Client",
            now - timedelta(days=6, hours=23),
        ),
    )
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(EmploymentExpirationSource(employment))
    await ingestion.run_source(FreelanceExpirationSource(freelance))

    async with sqlite_session_factory() as session, session.begin():
        favorite = await session.scalar(
            select(Opportunity).where(Opportunity.title == "Favorite Employment")
        )
        fallback = await session.scalar(
            select(Listing).where(Listing.external_id == "employment-fallback")
        )
        assert favorite is not None
        assert fallback is not None
        session.add(
            OpportunityUserState(
                opportunity_id=favorite.id,
                disposition=OpportunityDisposition.FAVORITE.value,
            )
        )
        fallback.first_seen_at = now - timedelta(days=31)

    summary = await StaleExpirationService(sqlite_session_factory).expire_stale(
        employment_days=30,
        freelance_days=7,
        now=now,
    )

    assert summary.expired_employment == 2
    assert summary.expired_freelance == 1
    assert summary.expired_total == 3
    assert summary.protected_favorites == 1
    async with sqlite_session_factory() as session:
        listings = {
            listing.external_id: listing
            for listing in await session.scalars(select(Listing).order_by(Listing.external_id))
        }
        assert listings["employment-old"].is_active is False
        assert listings["employment-fallback"].is_active is False
        assert listings["employment-fresh"].is_active is True
        assert listings["employment-favorite"].is_active is True
        assert listings["freelance-old"].is_active is False
        assert listings["freelance-fresh"].is_active is True
        expired = await session.scalar(
            select(Opportunity).where(Opportunity.title == "Old Employment")
        )
        assert expired is not None
        assert expired.status == OpportunityStatus.STALE.value


@pytest.mark.asyncio
async def test_expiration_promotes_fresh_cross_source_duplicate(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 8, 23, 12, tzinfo=UTC)
    old_rich = _listing(
        "old-rich",
        "Junior Full-Stack Developer",
        "Example Labs",
        now - timedelta(days=31),
        description="Rich React and Django description. " * 30,
    )
    fresh_sparse = deepcopy(old_rich)
    fresh_sparse.update(
        {
            "id": "fresh-sparse",
            "url": "https://alternate-expiration.test/jobs/fresh-sparse",
            "description": "Fresh React role.",
            "published_at": (now - timedelta(days=1)).isoformat(),
        }
    )
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(EmploymentExpirationSource((old_rich,)))
    await ingestion.run_source(AlternateEmploymentSource((fresh_sparse,)))

    summary = await StaleExpirationService(sqlite_session_factory).expire_stale(
        employment_days=30,
        freelance_days=7,
        now=now,
    )

    assert summary.expired_employment == 1
    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(select(Opportunity))
        listings = {
            listing.external_id: listing for listing in await session.scalars(select(Listing))
        }
        assert opportunity is not None
        assert opportunity.description == "Fresh React role."
        assert opportunity.status == OpportunityStatus.ACTIVE.value
        assert listings["old-rich"].is_active is False
        assert listings["fresh-sparse"].is_active is True
