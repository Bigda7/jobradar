import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.ingestion.service import IngestionService
from jobradar.sources.discovery import search_batch
from jobradar.sources.mock import MockSource
from jobradar.sources.workua import WorkUaSource

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_query_rotation_persists_across_new_adapters_and_unrelated_runs(
    postgres_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = tuple(f"https://www.work.ua/en/jobs-remote-query{i}/" for i in range(7))
    observed: list[str] = []

    async def empty(self: WorkUaSource, url: str) -> list:
        observed.append(url)
        return []

    monkeypatch.setattr(WorkUaSource, "_fetch_search_cards", empty)
    service = IngestionService(postgres_session_factory)
    for index in range(3):
        source = WorkUaSource(search_urls=urls, max_searches_per_run=3, max_pages_per_search=1)
        await service.run_source(source)
        assert observed[index * 3 : (index + 1) * 3] == list(search_batch(urls, 3, index, pinned=1))
        await service.run_source(MockSource())
    assert set(observed) == set(urls)
