from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    Opportunity,
    OpportunityUserState,
    Source,
    SourceRun,
)
from jobradar.domain.enums import OpportunityDisposition, OpportunityKind, RunStatus
from jobradar.ingestion.deduplication import CrossSourceDeduplicationService
from jobradar.ingestion.service import IngestionService, jittered_poll_interval_seconds
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


class AlternateMockSource(MockSource):
    name = "alternate_mock"
    display_name = "Alternate Mock Source"


class DouJobsMockSource(MockSource):
    name = "dou_jobs"
    display_name = "DOU Jobs"


class GreenhouseMockSource(MockSource):
    name = "greenhouse"
    display_name = "Greenhouse"


class FailingMockSource(MockSource):
    async def fetch(self):  # type: ignore[no-untyped-def]
        yield self._raw_listing(self._listings[0])
        raise RuntimeError("Simulated interrupted crawl")

    @staticmethod
    def _raw_listing(item):  # type: ignore[no-untyped-def]
        from jobradar.domain.models import RawListing

        return RawListing(
            external_id=str(item["id"]),
            source_url=str(item["url"]),
            payload=dict(item),
        )


class RollingFeedMockSource(MockSource):
    deactivate_missing_listings = False


class SecretFailingMockSource(MockSource):
    async def fetch(self):  # type: ignore[no-untyped-def]
        if False:
            yield
        raise RuntimeError("Request failed: https://api.example.test/jobs?api_key=secret-api-key")


class CacheAwareMockSource(MockSource):
    cached_before_fetch = False

    async def fetch(self):  # type: ignore[no-untyped-def]
        self.cached_before_fetch = self.cached_listing("mock-001") is not None
        detail_fetched_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
        async for listing in super().fetch():
            yield listing.model_copy(update={"detail_fetched_at": detail_fetched_at})


@pytest.mark.asyncio
async def test_repeated_run_is_idempotent(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)

    first = await service.run_source(MockSource())
    second = await service.run_source(MockSource())

    assert first.status is RunStatus.SUCCEEDED
    assert first.created == 2
    assert second.status is RunStatus.SUCCEEDED
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 2

    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Source)) == 1
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 2
        assert await session.scalar(select(func.count()).select_from(Listing)) == 2
        assert await session.scalar(select(func.count()).select_from(SourceRun)) == 2


@pytest.mark.asyncio
async def test_listing_detail_cache_is_persisted_and_primed_for_the_next_run(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    first_source = CacheAwareMockSource()
    second_source = CacheAwareMockSource()

    await service.run_source(first_source)
    await service.run_source(second_source)

    assert first_source.cached_before_fetch is False
    assert second_source.cached_before_fetch is True
    async with sqlite_session_factory() as session:
        listing = await session.scalar(select(Listing).where(Listing.external_id == "mock-001"))
        assert listing is not None
        assert listing.detail_fetched_at is not None
        assert listing.detail_fetched_at.isoformat() == "2026-08-23T12:00:00"


@pytest.mark.asyncio
async def test_changed_listing_updates_existing_opportunity(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    changed_listings = deepcopy(DEFAULT_LISTINGS)
    changed_listings[0]["description"] = "Updated job description."
    result = await service.run_source(MockSource(changed_listings))

    assert result.created == 0
    assert result.updated == 1
    assert result.unchanged == 1

    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(
            select(Opportunity).where(Opportunity.title == "Junior Full-Stack Developer")
        )
        assert opportunity is not None
        assert opportunity.description == "Updated job description."
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 2


@pytest.mark.asyncio
async def test_source_synchronization_disables_unconfigured_source(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    await service.synchronize_enabled_sources(())

    async with sqlite_session_factory() as session:
        source = await session.scalar(select(Source).where(Source.name == "mock"))
        assert source is not None
        assert source.enabled is False

    matching_summary = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    assert matching_summary.evaluated == 0
    assert matching_summary.unchanged == 0


@pytest.mark.asyncio
async def test_source_polling_interval_uses_last_run_time(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    source = MockSource()
    assert await service.is_source_due(source.name, 3600) is True

    await service.run_source(source)
    now = datetime.now(UTC)

    assert await service.is_source_due(source.name, 3600, jitter_ratio=0, now=now) is False
    assert (
        await service.is_source_due(
            source.name,
            3600,
            jitter_ratio=0,
            now=now + timedelta(hours=1, seconds=1),
        )
        is True
    )


def test_source_polling_jitter_is_stable_bounded_and_source_specific() -> None:
    last_run_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    djinni_interval = jittered_poll_interval_seconds("djinni", 3600, last_run_at)
    repeated_interval = jittered_poll_interval_seconds("djinni", 3600, last_run_at)
    freelancer_interval = jittered_poll_interval_seconds("freelancer", 3600, last_run_at)

    assert 3060 <= djinni_interval <= 4140
    assert repeated_interval == djinni_interval
    assert freelancer_interval != djinni_interval


@pytest.mark.asyncio
async def test_source_polling_uses_stable_jittered_due_time(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    source = MockSource()
    await service.run_source(source)
    async with sqlite_session_factory() as session:
        last_run_at = await session.scalar(
            select(Source.last_run_at).where(Source.name == source.name)
        )
    assert last_run_at is not None
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=UTC)
    interval = jittered_poll_interval_seconds(source.name, 3600, last_run_at)

    assert (
        await service.is_source_due(
            source.name,
            3600,
            now=last_run_at + timedelta(seconds=interval - 1),
        )
        is False
    )
    assert (
        await service.is_source_due(
            source.name,
            3600,
            now=last_run_at + timedelta(seconds=interval),
        )
        is True
    )


@pytest.mark.asyncio
async def test_successful_crawl_deactivates_missing_listings_and_reactivates_them(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    partial_inventory = await service.run_source(MockSource((DEFAULT_LISTINGS[0],)))

    assert partial_inventory.status is RunStatus.SUCCEEDED
    assert partial_inventory.deactivated == 1
    async with sqlite_session_factory() as session:
        inactive = await session.scalar(select(Listing).where(Listing.external_id == "mock-002"))
        assert inactive is not None
        assert inactive.is_active is False

    restored_inventory = await service.run_source(MockSource())

    assert restored_inventory.deactivated == 0
    async with sqlite_session_factory() as session:
        restored = await session.scalar(select(Listing).where(Listing.external_id == "mock-002"))
        assert restored is not None
        assert restored.is_active is True


@pytest.mark.asyncio
async def test_interrupted_crawl_does_not_deactivate_missing_listings(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    result = await service.run_source(FailingMockSource())

    assert result.status is RunStatus.FAILED
    assert result.deactivated == 0
    async with sqlite_session_factory() as session:
        listings = list(await session.scalars(select(Listing).order_by(Listing.external_id)))
        assert [listing.is_active for listing in listings] == [True, True]


@pytest.mark.asyncio
async def test_source_errors_are_redacted_before_database_storage(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    result = await IngestionService(sqlite_session_factory).run_source(SecretFailingMockSource())

    assert result.status is RunStatus.FAILED
    async with sqlite_session_factory() as session:
        source = await session.scalar(select(Source).where(Source.name == "mock"))
        run = await session.scalar(select(SourceRun))
        assert source is not None
        assert run is not None
        assert source.last_error is not None
        assert run.error_message is not None
        assert "secret-api-key" not in source.last_error
        assert "secret-api-key" not in run.error_message
        assert "[REDACTED]" in source.last_error


@pytest.mark.asyncio
async def test_empty_snapshot_is_blocked_before_deactivation(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource())

    result = await service.run_source(MockSource(()))

    assert result.status is RunStatus.FAILED
    assert result.deactivated == 0
    async with sqlite_session_factory() as session:
        listings = list(await session.scalars(select(Listing).order_by(Listing.external_id)))
        assert [listing.is_active for listing in listings] == [True, True]


@pytest.mark.asyncio
async def test_snapshot_losing_more_than_eighty_percent_is_blocked(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    inventory = []
    for index in range(10):
        listing = deepcopy(DEFAULT_LISTINGS[0])
        listing.update(
            {
                "id": f"guard-{index}",
                "url": f"https://example.com/jobs/guard-{index}",
                "title": f"Remote Python Developer {index}",
            }
        )
        inventory.append(listing)
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource(inventory))

    result = await service.run_source(MockSource((inventory[0],)))

    assert result.status is RunStatus.FAILED
    assert result.deactivated == 0
    async with sqlite_session_factory() as session:
        active_count = await session.scalar(
            select(func.count()).select_from(Listing).where(Listing.is_active.is_(True))
        )
        assert active_count == 10


@pytest.mark.asyncio
async def test_snapshot_losing_exactly_eighty_percent_is_reconciled(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    inventory = []
    for index in range(5):
        listing = deepcopy(DEFAULT_LISTINGS[0])
        listing.update(
            {
                "id": f"boundary-{index}",
                "url": f"https://example.com/jobs/boundary-{index}",
                "title": f"Remote React Developer {index}",
            }
        )
        inventory.append(listing)
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource(inventory))

    result = await service.run_source(MockSource((inventory[0],)))

    assert result.status is RunStatus.SUCCEEDED
    assert result.deactivated == 4


@pytest.mark.asyncio
async def test_rolling_feed_does_not_deactivate_items_omitted_from_limited_window(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(RollingFeedMockSource())

    result = await service.run_source(RollingFeedMockSource((DEFAULT_LISTINGS[0],)))

    assert result.status is RunStatus.SUCCEEDED
    assert result.deactivated == 0
    async with sqlite_session_factory() as session:
        listings = list(await session.scalars(select(Listing).order_by(Listing.external_id)))
        assert [listing.is_active for listing in listings] == [True, True]


@pytest.mark.asyncio
async def test_cross_source_title_company_duplicate_uses_one_opportunity(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_listing = deepcopy(DEFAULT_LISTINGS[0])
    second_listing = deepcopy(DEFAULT_LISTINGS[0])
    second_listing.update(
        {
            "id": "alternate-001",
            "url": "https://alternate.example/jobs/react-developer",
            "title": "junior full-stack developer",
            "company": "EXAMPLE LABS",
            "description": "A second platform copy with a different description.",
        }
    )
    service = IngestionService(sqlite_session_factory)

    first = await service.run_source(MockSource((first_listing,)))
    second = await service.run_source(AlternateMockSource((second_listing,)))

    assert first.created == 1
    assert second.created == 0
    assert second.duplicates == 1
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert await session.scalar(select(func.count()).select_from(Listing)) == 2
        opportunity = await session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.title == "Junior Full-Stack Developer"
        assert opportunity.company == "Example Labs"
        assert opportunity.description == first_listing["description"]

    first_matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    assert first_matching.evaluated == 1

    second_listing["description"] = "The secondary platform changed its description."
    update_result = await service.run_source(AlternateMockSource((second_listing,)))
    second_matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    assert update_result.updated == 1
    assert second_matching.evaluated == 0
    assert second_matching.unchanged == 1


@pytest.mark.asyncio
async def test_cross_source_duplicate_promotes_richer_listing_to_canonical(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sparse = deepcopy(DEFAULT_LISTINGS[0])
    sparse.update(
        {
            "description": "React role.",
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_period": None,
        }
    )
    rich = deepcopy(sparse)
    rich.update(
        {
            "id": "alternate-rich",
            "url": "https://alternate.example/jobs/rich",
            "description": "Build and test React and Django applications. " * 20,
            "salary_min": "1200",
            "salary_max": "1800",
            "salary_currency": "USD",
            "salary_period": "month",
        }
    )
    service = IngestionService(sqlite_session_factory)

    await service.run_source(MockSource((sparse,)))
    result = await service.run_source(AlternateMockSource((rich,)))

    assert result.duplicates == 1
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.description == rich["description"]
        assert opportunity.salary_min == 1200
        listings = list(
            await session.scalars(
                select(Listing).order_by(Listing.quality_score.desc(), Listing.id.asc())
            )
        )
        assert listings[0].external_id == "alternate-rich"
        evaluation = await session.scalar(select(MatchEvaluation))
        assert evaluation is not None
        assert evaluation.listing_content_hash == listings[0].content_hash


@pytest.mark.asyncio
async def test_direct_ats_listing_has_canonical_priority_over_richer_aggregator(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    aggregator_listing = deepcopy(DEFAULT_LISTINGS[0])
    aggregator_listing.update(
        {
            "description": "A very rich DOU description. " * 100,
            "url": "https://jobs.dou.ua/companies/example/vacancies/101",
        }
    )
    ats_listing = deepcopy(aggregator_listing)
    ats_listing.update(
        {
            "id": "greenhouse-101",
            "description": "Direct ATS description.",
            "url": "https://job-boards.greenhouse.io/example/jobs/101",
        }
    )
    ingestion = IngestionService(sqlite_session_factory)

    await ingestion.run_source(DouJobsMockSource((aggregator_listing,)))
    result = await ingestion.run_source(GreenhouseMockSource((ats_listing,)))

    assert result.duplicates == 1
    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(select(Opportunity))
        assert opportunity is not None
        opportunity_id = opportunity.id
        assert opportunity.description == aggregator_listing["description"]
    source_url = await OpportunityStateService(sqlite_session_factory).source_url(opportunity_id)
    assert source_url == "https://job-boards.greenhouse.io/example/jobs/101"


@pytest.mark.asyncio
async def test_force_matching_recalculates_current_version_evaluation(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(MockSource((DEFAULT_LISTINGS[0],)))
    matching = MatchingService(sqlite_session_factory)
    first = await matching.evaluate(BOHDAN_PROFILE)
    assert first.evaluated == 1

    async with sqlite_session_factory() as session, session.begin():
        evaluation = await session.scalar(select(MatchEvaluation))
        assert evaluation is not None
        evaluation.score = -1

    unchanged = await matching.evaluate(BOHDAN_PROFILE)
    rescored = await matching.evaluate(BOHDAN_PROFILE, force=True)

    assert unchanged.unchanged == 1
    assert rescored.evaluated == 1
    assert rescored.unchanged == 0
    async with sqlite_session_factory() as session:
        evaluation = await session.scalar(select(MatchEvaluation))
        assert evaluation is not None
        assert evaluation.score >= 0


@pytest.mark.asyncio
async def test_existing_cross_source_duplicates_are_merged_with_user_state(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestionService(sqlite_session_factory)
    await service.run_source(MockSource((DEFAULT_LISTINGS[0],)))
    async with sqlite_session_factory() as session, session.begin():
        source = await session.scalar(select(Source).where(Source.name == "mock"))
        assert source is not None
        duplicate = Opportunity(
            kind=OpportunityKind.EMPLOYMENT.value,
            canonical_key="duplicate-key",
            title="JUNIOR FULL-STACK DEVELOPER",
            company="example labs",
            description="Duplicate description",
            work_mode="remote",
        )
        session.add(duplicate)
        await session.flush()
        session.add_all(
            (
                Listing(
                    source_id=source.id,
                    opportunity_id=duplicate.id,
                    external_id="legacy-duplicate",
                    source_url="https://legacy.example/jobs/duplicate",
                    canonical_url="https://legacy.example/jobs/duplicate",
                    content_hash="b" * 64,
                    raw_data={},
                ),
                OpportunityUserState(
                    opportunity_id=duplicate.id,
                    disposition=OpportunityDisposition.FAVORITE.value,
                ),
            )
        )

    summary = await CrossSourceDeduplicationService(sqlite_session_factory).merge_existing()

    assert summary.duplicate_groups == 1
    assert summary.merged_opportunities == 1
    async with sqlite_session_factory() as session:
        opportunities = list(await session.scalars(select(Opportunity)))
        assert len(opportunities) == 1
        assert await session.scalar(select(func.count()).select_from(Listing)) == 2
        state = await session.get(OpportunityUserState, opportunities[0].id)
        assert state is not None
        assert state.disposition == OpportunityDisposition.FAVORITE.value
