from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from jobradar.api.app import create_app
from jobradar.db.models import Listing, Opportunity, Source, SourceRun
from jobradar.domain.models import RawListing
from jobradar.ingestion.service import IngestionService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cross_source_lookup_loads_only_matching_identity_candidates(
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(postgres_session_factory).run_source(MockSource((DEFAULT_LISTINGS[0],)))
    async with postgres_session_factory() as session, session.begin():
        source_id = await session.scalar(select(Source.id).where(Source.name == "mock"))
        target_id = await session.scalar(select(Opportunity.id))
        assert source_id is not None
        assert target_id is not None
        searching_source = Source(name="alternate", display_name="Alternate source")
        session.add(searching_source)
        await session.flush()
        searching_source_id = searching_source.id
        now = datetime.now(UTC)
        unrelated_ids = (
            await session.scalars(
                insert(Opportunity).returning(Opportunity.id),
                [
                    {
                        "kind": "employment",
                        "canonical_key": f"{index:064x}",
                        "title": f"Unrelated role {index}",
                        "company": "Unrelated Company",
                        "location_text": "Remote Europe",
                        "work_mode": "remote",
                        "published_at": now,
                        "description": (
                            "Unrelated description with enough words for a realistic record."
                        ),
                    }
                    for index in range(1, 1001)
                ],
            )
        ).all()
        await session.execute(
            insert(Listing),
            [
                {
                    "source_id": source_id,
                    "opportunity_id": opportunity_id,
                    "external_id": f"unrelated-{index}",
                    "source_url": f"https://example.com/jobs/unrelated-{index}",
                    "canonical_url": f"https://example.com/jobs/unrelated-{index}",
                    "content_hash": f"{index:064x}",
                    "raw_data": {},
                    "normalized_data": {},
                    "is_active": True,
                }
                for index, opportunity_id in enumerate(unrelated_ids, start=1)
            ],
        )

    raw_listing = RawListing(
        external_id="alternate-performance",
        source_url="https://alternate.example/jobs/performance",
        payload=DEFAULT_LISTINGS[0],
    )
    normalized = MockSource().normalize(raw_listing)
    loaded_opportunities = 0

    def record_loaded(_session: object, instance: object) -> None:
        nonlocal loaded_opportunities
        if isinstance(instance, Opportunity):
            loaded_opportunities += 1

    async with postgres_session_factory() as session:
        event.listen(session.sync_session, "loaded_as_persistent", record_loaded)
        duplicate = await IngestionService._find_cross_source_duplicate(
            session, searching_source_id, normalized
        )

    assert duplicate is not None
    assert duplicate.id == target_id
    assert loaded_opportunities == 1


@pytest.mark.asyncio
async def test_sources_endpoint_uses_bounded_query_count(
    postgres_engine: AsyncEngine,
    postgres_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_session_factory() as session, session.begin():
        for index in range(4):
            source = Source(name=f"source-{index}", display_name=f"Source {index}")
            session.add(source)
            await session.flush()
            for status in ("succeeded", "failed"):
                session.add(
                    SourceRun(
                        source_id=source.id,
                        status=status,
                        started_at=datetime(2026, 9, 28, tzinfo=UTC),
                    )
                )
        session.add(Source(name="source-without-runs", display_name="Source without runs"))

    select_count = 0

    def record_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal select_count
        if statement.lstrip().lower().startswith("select"):
            select_count += 1

    event.listen(postgres_engine.sync_engine, "before_cursor_execute", record_query)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app(postgres_session_factory)),
            base_url="http://test",
        ) as client:
            response = await client.get("/sources")
    finally:
        event.remove(postgres_engine.sync_engine, "before_cursor_execute", record_query)

    assert response.status_code == 200
    assert [item["last_run_status"] for item in response.json()] == [
        "failed",
        "failed",
        "failed",
        "failed",
        None,
    ]
    assert select_count == 1
