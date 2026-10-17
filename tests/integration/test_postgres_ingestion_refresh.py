import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.unit.test_ingestion_refresh import assert_refresh_is_immediately_matchable

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", ["legacy", "reopened"])
async def test_postgres_refresh_is_immediately_matchable(
    postgres_session_factory: async_sessionmaker[AsyncSession], scenario: str
) -> None:
    await assert_refresh_is_immediately_matchable(postgres_session_factory, scenario)
