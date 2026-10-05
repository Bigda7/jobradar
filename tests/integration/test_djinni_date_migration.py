import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, NotificationDelivery, Opportunity, Source
from jobradar.domain.models import NormalizedOpportunity
from jobradar.domain.normalization import build_content_hash
from jobradar.ingestion.canonical import listing_quality_score

pytestmark = pytest.mark.integration
MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/20261005_0020_source_update_dates.py"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_canonical", [False, True])
@pytest.mark.parametrize(
    "rss_date",
    ["Sat, 22 Aug 2026 15:36:45 +0300", "invalid", None],
    ids=["rss-bump", "invalid-rss-date", "legacy-page-date"],
)
async def test_djinni_date_repair_preserves_ids_sent_deliveries_and_other_source_dates(
    postgres_engine: AsyncEngine,
    postgres_session_factory: async_sessionmaker[AsyncSession],
    shared_canonical: bool,
    rss_date: str | None,
) -> None:
    date = datetime(2026, 8, 22, 12, 36, 45, tzinfo=UTC)
    original = NormalizedOpportunity(title="Junior Python Developer", published_at=date)
    payload = {"datePosted": date.isoformat()}
    if rss_date is not None:
        payload["rss"] = {"pubDate": rss_date}
    expected_update = date if rss_date and rss_date != "invalid" else None
    async with postgres_session_factory() as session, session.begin():
        djinni = Source(name="djinni", display_name="Djinni", enabled=True)
        other = Source(name="other", display_name="Other", enabled=True)
        session.add_all([djinni, other])
        await session.flush()
        one = Opportunity(title=original.title, canonical_key="one", published_at=date)
        two = Opportunity(title="Other", canonical_key="two", published_at=date)
        session.add_all([one, two])
        await session.flush()
        shared = Listing(
            source_id=other.id,
            opportunity_id=one.id,
            external_id="shared",
            source_url="https://example.com/shared",
            canonical_url="https://example.com/shared",
            content_hash="shared",
            raw_data={},
            normalized_data={
                **original.model_dump(mode="json"),
                "description": "Richer other-source description",
            },
            published_at=date,
            quality_score=1000,
            is_active=shared_canonical,
        )
        affected = Listing(
            source_id=djinni.id,
            opportunity_id=one.id,
            external_id="844408",
            source_url="https://djinni.co/jobs/844408-python/",
            canonical_url="https://djinni.co/jobs/844408-python/",
            content_hash="old",
            raw_data=payload,
            normalized_data=original.model_dump(mode="json"),
            published_at=date,
            quality_score=listing_quality_score(original),
        )
        unrelated = Listing(
            source_id=other.id,
            opportunity_id=two.id,
            external_id="other",
            source_url="https://example.com/other",
            canonical_url="https://example.com/other",
            content_hash="untouched",
            raw_data={},
            normalized_data=original.model_dump(mode="json"),
            published_at=date,
        )
        session.add_all([shared, affected, unrelated])
        await session.flush()
        sent = NotificationDelivery(
            opportunity_id=one.id,
            profile_id="test",
            channel="telegram",
            event_key="sent",
            status="sent",
            sent_at=date,
        )
        session.add(sent)
        await session.flush()
        affected_id, unrelated_id, opportunity_id, delivery_id = (
            affected.id,
            unrelated.id,
            one.id,
            sent.id,
        )

    spec = importlib.util.spec_from_file_location("djinni_date_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(connection):  # type: ignore[no-untyped-def]
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    async with postgres_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE listings DROP COLUMN source_updated_at"))
        await connection.execute(text("ALTER TABLE opportunities DROP COLUMN source_updated_at"))
        await connection.run_sync(migrate)

    async with postgres_session_factory() as session:
        listing = await session.get(Listing, affected_id)
        assert listing is not None
        assert listing.published_at is None and listing.source_updated_at == expected_update
        assert ("datePosted" in listing.raw_data) is (rss_date is None)
        assert listing.content_hash == build_content_hash(
            NormalizedOpportunity.model_validate(listing.normalized_data), listing.raw_data
        )
        untouched = await session.get(Listing, unrelated_id)
        assert (
            untouched is not None
            and untouched.published_at == date
            and untouched.content_hash == "untouched"
        )
        opportunity = await session.get(Opportunity, opportunity_id)
        assert opportunity is not None
        assert opportunity.published_at == (date if shared_canonical else None)
        assert opportunity.source_updated_at == (None if shared_canonical else expected_update)
        assert opportunity.description == (
            "Richer other-source description" if shared_canonical else None
        )
        delivery = await session.get(NotificationDelivery, delivery_id)
        assert delivery is not None and delivery.status == "sent" and delivery.sent_at == date
        assert len((await session.scalars(select(Listing))).all()) == 3
