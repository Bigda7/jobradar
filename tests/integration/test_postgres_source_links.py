import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, Source
from jobradar.ingestion.link_filter import trusted_listing_condition
from jobradar.ingestion.service import IngestionService
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_listing_filter_blocks_stored_lookalike_host(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(postgres_session_factory).run_source(MockSource())
    async with postgres_session_factory() as session, session.begin():
        opportunity_id = await session.scalar(select(Opportunity.id).limit(1))
        assert opportunity_id is not None
        source = Source(name="djinni", display_name="Djinni", enabled=True)
        session.add(source)
        await session.flush()
        for external_id, url in (
            ("trusted", "https://djinni.co/jobs/1/"),
            ("untrusted", "https://djinni.co.evil.test/jobs/2/"),
        ):
            session.add(
                Listing(
                    source_id=source.id,
                    opportunity_id=opportunity_id,
                    external_id=external_id,
                    source_url=url,
                    canonical_url=url,
                    content_hash=external_id,
                    raw_data={},
                    normalized_data={},
                    is_active=True,
                )
            )

    async with postgres_session_factory() as session:
        urls = list(
            await session.scalars(
                select(Listing.source_url)
                .join(Source, Source.id == Listing.source_id)
                .where(Source.name == "djinni", trusted_listing_condition())
            )
        )

    assert urls == ["https://djinni.co/jobs/1/"]
