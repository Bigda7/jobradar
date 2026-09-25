import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobradar.db.locks import try_transaction_advisory_lock
from jobradar.db.models import Listing, Opportunity, Source
from jobradar.domain.enums import RunStatus
from jobradar.ingestion.service import IngestionService
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration
TEST_ADVISORY_LOCK_KEY = 0x4A4F425241444153


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


@pytest.mark.asyncio
async def test_postgres_advisory_lock_prevents_overlapping_worker_cycles(
    postgres_engine: AsyncEngine,
) -> None:
    async with try_transaction_advisory_lock(
        postgres_engine,
        TEST_ADVISORY_LOCK_KEY,
    ) as first_acquired:
        assert first_acquired is True
        async with try_transaction_advisory_lock(
            postgres_engine,
            TEST_ADVISORY_LOCK_KEY,
        ) as second_acquired:
            assert second_acquired is False

    async with try_transaction_advisory_lock(
        postgres_engine,
        TEST_ADVISORY_LOCK_KEY,
    ) as acquired_after_release:
        assert acquired_after_release is True


@pytest.mark.asyncio
async def test_postgres_opportunity_search_indexes_use_trigrams(
    postgres_engine: AsyncEngine,
) -> None:
    index_names = {
        "ix_opportunities_title_trgm",
        "ix_opportunities_company_trgm",
        "ix_opportunities_description_trgm",
    }
    async with postgres_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'opportunities'
                      AND indexname = ANY(:index_names)
                    """
                ),
                {"index_names": list(index_names)},
            )
        ).all()

    definitions = {name: definition for name, definition in rows}
    assert definitions.keys() == index_names
    assert all("USING gin" in definition for definition in definitions.values())
    assert all("gin_trgm_ops" in definition for definition in definitions.values())
