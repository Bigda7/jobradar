from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.unit.test_dou_discovery import rss

from jobradar.db.models import Listing, Opportunity, Source, SourceRun
from jobradar.domain.enums import RunStatus
from jobradar.ingestion.service import IngestionService
from jobradar.sources.dou_jobs import DouJobsSource

pytestmark = pytest.mark.integration


async def test_dou_budget_persists_across_ingestion_and_adapter_recreation(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC).timestamp()
    async with postgres_session_factory() as session, session.begin():
        session.add(
            Source(name="dou_jobs", display_name="DOU Jobs", request_budget={"dou_rss": [now] * 89})
        )
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=rss(("820001", "Developer в Example, remote", "React")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ingestion = IngestionService(postgres_session_factory)
        first = await ingestion.run_source(
            DouJobsSource(
                client=client,
                additional_feed_urls=("https://jobs.dou.ua/vacancies/feeds/?remote&search=python",),
            )
        )
        second = await ingestion.run_source(DouJobsSource(client=client))
    assert first.status is RunStatus.PARTIAL
    assert first.created == 1 and first.pages == 1 and first.errors == 1
    assert second.status is RunStatus.FAILED
    assert second.created == 0 and second.pages == 0 and second.errors == 1
    assert len(calls) == 1
    async with postgres_session_factory() as session:
        source = await session.scalar(select(Source).where(Source.name == "dou_jobs"))
        assert source is not None and len(source.request_budget["dou_rss"]) == 90
        assert await session.scalar(select(func.count()).select_from(Listing)) == 1
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert await session.scalar(select(func.count()).select_from(SourceRun)) == 2


async def test_dou_repeated_ingestion_above_old_cap_is_idempotent(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = rss(
        *(
            (str(830000 + index), f"Developer в Company {index}, remote", f"Python project {index}")
            for index in range(130)
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        ingestion = IngestionService(postgres_session_factory)
        first = await ingestion.run_source(DouJobsSource(client=client))
        second = await ingestion.run_source(DouJobsSource(client=client))
    assert first.status is second.status is RunStatus.SUCCEEDED
    assert first.created == 130 and first.discovered == 130 and not first.limit_reached
    assert second.created == 0 and second.unchanged == 130 and not second.limit_reached
    async with postgres_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 130
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 130
        source = await session.scalar(select(Source).where(Source.name == "dou_jobs"))
        assert source is not None and len(source.request_budget["dou_rss"]) == 2
