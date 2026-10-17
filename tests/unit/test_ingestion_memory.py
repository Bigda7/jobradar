import asyncio

import pytest
from sqlalchemy import event, select, text

from jobradar.db.models import Source
from jobradar.ingestion.service import IngestionService
from jobradar.sources.base import CachedListing
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


async def test_cache_reads_only_required_columns_and_keeps_inactive_records(
    sqlite_engine, sqlite_session_factory
):
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())
    async with sqlite_session_factory() as session, session.begin():
        source_id = await session.scalar(select(Source.id).where(Source.name == "mock"))
        await session.execute(text("UPDATE listings SET is_active=false"))
    queries = []

    def record_sql(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(sqlite_engine.sync_engine, "before_cursor_execute", record_sql)
    try:
        cache = await service._load_listing_cache(source_id)
    finally:
        event.remove(sqlite_engine.sync_engine, "before_cursor_execute", record_sql)
    assert set(cache) == {"mock-001", "mock-002"}
    assert cache["mock-001"].payload == DEFAULT_LISTINGS[0]
    assert cache["mock-002"].detail_fetched_at is None
    assert len(queries) == 1
    assert "normalized_data" not in queries[0]
    assert "quality_score" not in queries[0]
    assert "raw_data" in queries[0] and "detail_fetched_at" in queries[0]


@pytest.mark.parametrize("fail", [False, True])
async def test_cache_cursor_is_batched_and_always_closed(fail):
    class Rows:
        closed = False

        def __aiter__(self):
            return self.iterate()

        async def iterate(self):
            yield "one", {"description": "Preserved"}, None
            if fail:
                raise RuntimeError("Synthetic cursor failure")

        async def close(self):
            self.closed = True

    rows = Rows()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def stream(self, statement):
            assert list(statement.selected_columns.keys()) == [
                "external_id",
                "raw_data",
                "detail_fetched_at",
            ]
            assert statement.get_execution_options()["yield_per"] == 200
            return rows

    service = IngestionService(Session)
    if fail:
        with pytest.raises(RuntimeError, match="Synthetic cursor failure"):
            await service._load_listing_cache(1)
    else:
        assert (await service._load_listing_cache(1))["one"].payload == {"description": "Preserved"}
    assert rows.closed


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_adapter_cache_is_loaded_for_fetch_and_released_after_run(
    sqlite_session_factory, outcome
):
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    class SourceWithCache(MockSource):
        async def fetch(self):
            assert self.cached_listing("old") is None
            assert self.cached_listing("mock-001").payload == DEFAULT_LISTINGS[0]
            if outcome == "cancelled":
                raise asyncio.CancelledError
            if outcome == "failure":
                raise RuntimeError("Synthetic provider failure")
            async for listing in super().fetch():
                yield listing

    adapter = SourceWithCache()
    adapter.prime_listing_cache({"old": CachedListing({"description": "Old run"}, None)})
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await service.run_source(adapter)
    else:
        result = await service.run_source(adapter)
        assert result.errors == (1 if outcome == "failure" else 0)
    assert adapter.cached_listing("mock-001") is None
    assert adapter.cached_listing("old") is None
