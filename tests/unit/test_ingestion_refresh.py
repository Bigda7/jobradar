import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


async def assert_refresh_is_immediately_matchable(
    factory: async_sessionmaker[AsyncSession], scenario: str
) -> None:
    service = IngestionService(factory)
    source = MockSource([dict(DEFAULT_LISTINGS[0])])
    assert (await service.run_source(source)).created == 1
    async with factory() as session, session.begin():
        listing = await session.scalar(select(Listing))
        opportunity = await session.scalar(select(Opportunity))
        assert listing is not None and opportunity is not None
        identity = (listing.id, opportunity.id)
        if scenario == "legacy":
            await session.execute(text("UPDATE listings SET normalized_data=NULL"))
        else:
            listing.is_active = False
            opportunity.status = "stale"
    result = await service.run_source(source)
    assert result.discovered == 1 and result.errors == 0
    async with factory() as session:
        listing = await session.scalar(select(Listing))
        opportunity = await session.scalar(select(Opportunity))
        assert listing is not None and opportunity is not None
        assert (listing.id, opportunity.id) == identity
        assert listing.is_active and listing.normalized_data is not None
        assert opportunity.status == "active"
        assert opportunity.description == DEFAULT_LISTINGS[0]["description"]
    assert (await MatchingService(factory).evaluate(BOHDAN_PROFILE)).evaluated == 1


@pytest.mark.parametrize("scenario", ["legacy", "reopened"])
async def test_refresh_is_immediately_matchable(
    sqlite_session_factory: async_sessionmaker[AsyncSession], scenario: str
) -> None:
    await assert_refresh_is_immediately_matchable(sqlite_session_factory, scenario)
