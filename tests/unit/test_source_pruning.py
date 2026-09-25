from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, Source, SourceRun
from jobradar.ingestion.service import IngestionService
from jobradar.opportunities.pruning import SourcePruningService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


class RetiredSource(MockSource):
    name = "retired"
    display_name = "Retired"


class RetainedSource(MockSource):
    name = "retained"
    display_name = "Retained"


@pytest.mark.asyncio
async def test_pruning_removes_retired_only_opportunities_and_preserves_shared_ones(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    retired_only = deepcopy(DEFAULT_LISTINGS[0])
    retired_only["id"] = "retired-only"
    shared_retired = deepcopy(DEFAULT_LISTINGS[1])
    shared_retired["id"] = "shared-retired"
    shared_retained = deepcopy(DEFAULT_LISTINGS[1])
    shared_retained.update(
        {
            "id": "shared-retained",
            "url": "https://retained.test/jobs/shared-retained",
            "description": "Canonical content from the retained source.",
        }
    )
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(RetiredSource((retired_only, shared_retired)))
    await ingestion.run_source(RetainedSource((shared_retained,)))
    service = SourcePruningService(sqlite_session_factory)

    preview = await service.prune(("retired",))

    assert preview.matched_sources == 1
    assert preview.deleted_source_runs == 1
    assert preview.deleted_listings == 2
    assert preview.deleted_opportunities == 1
    assert preview.preserved_shared_opportunities == 1
    assert preview.applied is False
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 3
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 2
        assert await session.scalar(select(func.count()).select_from(SourceRun)) == 2

    applied = await service.prune(("retired",), apply=True)

    assert applied.applied is True
    async with sqlite_session_factory() as session:
        listings = list(await session.scalars(select(Listing)))
        opportunities = list(await session.scalars(select(Opportunity)))
        retired_source = await session.scalar(select(Source).where(Source.name == "retired"))
        retained_source = await session.scalar(select(Source).where(Source.name == "retained"))
        source_runs = list(await session.scalars(select(SourceRun)))
        assert [listing.external_id for listing in listings] == ["shared-retained"]
        assert [opportunity.title for opportunity in opportunities] == ["Junior React Developer"]
        assert opportunities[0].description == "Canonical content from the retained source."
        assert retired_source is None
        assert retained_source is not None
        assert [source_run.source_id for source_run in source_runs] == [retained_source.id]
