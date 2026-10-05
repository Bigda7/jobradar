from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source, SourceRun
from jobradar.ingestion.request_budget import DatabaseRequestBudget
from jobradar.ingestion.service import IngestionService
from jobradar.sources.request_budget import MemoryRequestBudget


@pytest.mark.asyncio
async def test_rss_window_is_shared_by_concurrent_reservations_and_expires() -> None:
    import asyncio

    clock = [1000.0]
    budget = MemoryRequestBudget(lambda: clock[0])
    assert sum(await asyncio.gather(*(budget.reserve("rss") for _ in range(101)))) == 100
    clock[0] += 59.999
    assert not await budget.reserve("rss")
    clock[0] += 0.001
    assert await budget.reserve("rss")


@pytest.mark.asyncio
async def test_metadata_has_separate_hourly_and_spacing_limits() -> None:
    clock = [1000.0]
    budget = MemoryRequestBudget(lambda: clock[0])
    for _ in range(100):
        assert await budget.reserve("metadata")
        assert not await budget.reserve("metadata")
        clock[0] += 2
    assert not await budget.reserve("metadata")
    assert await budget.reserve("rss")
    clock[0] = 4600.0
    assert await budget.reserve("metadata")


@pytest.mark.asyncio
async def test_database_budget_survives_new_adapter_and_failed_network_attempt(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC).timestamp()
    async with sqlite_session_factory() as session, session.begin():
        source = Source(
            name="djinni",
            display_name="Djinni",
            request_budget={
                "metadata": [now - 1000 + i * 2 for i in range(99)],
                "rss": [now] * 100,
            },
        )
        session.add(source)
        await session.flush()
        source_id = source.id
    first = DatabaseRequestBudget(sqlite_session_factory, source_id)
    assert await first.reserve("metadata")
    restarted = DatabaseRequestBudget(sqlite_session_factory, source_id)
    assert not await restarted.reserve("metadata")
    assert not await restarted.reserve("rss")
    async with sqlite_session_factory() as session:
        source = await session.get(Source, source_id)
        assert source is not None
        assert len(source.request_budget["metadata"]) == 100


@pytest.mark.asyncio
async def test_start_cadence_does_not_wait_another_interval_after_completion(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started = datetime.now(UTC) - timedelta(minutes=16)
    async with sqlite_session_factory() as session, session.begin():
        source = Source(
            name="djinni", display_name="Djinni", last_run_at=started + timedelta(minutes=9)
        )
        session.add(source)
        await session.flush()
        session.add(SourceRun(source_id=source.id, started_at=started, status="succeeded"))
    service = IngestionService(sqlite_session_factory)
    assert await service.is_source_due("djinni", 900, jitter_ratio=0, schedule_from_start=True)
    assert not await service.is_source_due("djinni", 900, jitter_ratio=0)
    async with sqlite_session_factory() as session:
        assert (await session.scalar(select(Source))).last_run_at is not None
