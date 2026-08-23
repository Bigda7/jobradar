import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, Source
from jobradar.domain.enums import RunStatus
from jobradar.ingestion.service import IngestionService
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_upsert_and_concurrent_runs_do_not_duplicate_rows(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(postgres_session_factory)

    first, second = await asyncio.gather(
        service.run_source(MockSource()),
        service.run_source(MockSource()),
    )

    assert first.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}
    assert second.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}

    third = await service.run_source(MockSource())
    assert third.status is RunStatus.SUCCEEDED
    assert third.created == 0
    assert third.updated == 0
    assert third.unchanged == 2

    async with postgres_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Source)) == 1
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 2
        assert await session.scalar(select(func.count()).select_from(Listing)) == 2
