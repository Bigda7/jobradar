from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, Source
from jobradar.domain.enums import RunStatus
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.currency import NbuExchangeRateClient
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import TelegramClient
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.dou_jobs import DouJobsSource
from jobradar.sources.link_policy import is_trusted_source_link
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource
from jobradar.sources.robota_ua import RobotaUaSource
from jobradar.sources.workua import WorkUaSource


class RestrictedMockSource(MockSource):
    name = "restricted_mock"
    allowed_listing_hosts = frozenset({"trusted.example"})


@pytest.mark.parametrize(
    ("url", "hosts"),
    (
        ("https://djinni.co/jobs/123/", frozenset({"djinni.co"})),
        ("https://WWW.WORK.UA/en/jobs/123/", frozenset({"www.work.ua"})),
        ("https://jobs.dou.ua:443/companies/example/vacancies/123/", frozenset({"jobs.dou.ua"})),
    ),
)
def test_trusted_source_link_accepts_exact_https_hosts(url: str, hosts: frozenset[str]) -> None:
    assert is_trusted_source_link(url, hosts)


@pytest.mark.parametrize(
    "url",
    (
        "http://djinni.co/jobs/123/",
        "https://djinni.co.evil.example/jobs/123/",
        "https://sub.djinni.co/jobs/123/",
        "https://djinni.co@evil.example/jobs/123/",
        "https://user@djinni.co/jobs/123/",
        "https://djinni.co:8443/jobs/123/",
        "https://djinni.co:invalid/jobs/123/",
        "https://djinni.co./jobs/123/",
        "https://djinni.co/jobs/123/\n",
        "//djinni.co/jobs/123/",
    ),
)
def test_trusted_source_link_rejects_unexpected_origins(url: str) -> None:
    assert not is_trusted_source_link(url, frozenset({"djinni.co"}))


def test_active_sources_declare_expected_listing_hosts() -> None:
    assert DjinniSource.allowed_listing_hosts == frozenset({"djinni.co", "www.djinni.co"})
    assert DouJobsSource.allowed_listing_hosts == frozenset({"jobs.dou.ua"})
    assert WorkUaSource.allowed_listing_hosts == frozenset({"work.ua", "www.work.ua"})
    assert RobotaUaSource.allowed_listing_hosts == frozenset({"robota.ua", "www.robota.ua"})


@pytest.mark.asyncio
async def test_untrusted_listing_is_not_ingested_and_source_run_is_partial(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    listings = deepcopy(DEFAULT_LISTINGS)
    listings[0]["url"] = "https://trusted.example/jobs/1/"
    listings[1]["url"] = "https://trusted.example.evil.test/jobs/2/"

    result = await IngestionService(sqlite_session_factory).run_source(
        RestrictedMockSource(listings)
    )

    assert result.status is RunStatus.PARTIAL
    assert result.created == 1
    assert result.errors == 1
    assert result.warnings == ["Rejected 1 listing links outside allowed source domains."]
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 1
        source = await session.scalar(select(Source).where(Source.name == "restricted_mock"))
        assert source is not None
        assert source.last_error == result.warnings[0]


@pytest.mark.asyncio
async def test_existing_untrusted_link_is_not_selected_for_telegram_or_bot(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    async with sqlite_session_factory() as session, session.begin():
        opportunity_id = await session.scalar(select(Opportunity.id).limit(1))
        assert opportunity_id is not None
        source = Source(name="djinni", display_name="Djinni", enabled=True)
        session.add(source)
        await session.flush()
        session.add(
            Listing(
                source_id=source.id,
                opportunity_id=opportunity_id,
                external_id="unsafe-1",
                source_url="https://djinni.co.evil.test/jobs/1/",
                canonical_url="https://djinni.co.evil.test/jobs/1/",
                content_hash="unsafe-content-hash",
                raw_data={},
                normalized_data={},
                quality_score=100000,
                is_active=True,
            )
        )

    candidates = await NotificationService(
        sqlite_session_factory,
        TelegramClient(bot_token="test-token", chat_id=123),
        NbuExchangeRateClient(),
    ).load_candidates(BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold)
    bot_source_url = await OpportunityStateService(sqlite_session_factory).source_url(
        opportunity_id
    )

    candidate = next(item for item in candidates if item.opportunity_id == opportunity_id)
    assert candidate.source_url.startswith("https://example.com/")
    assert bot_source_url == candidate.source_url
