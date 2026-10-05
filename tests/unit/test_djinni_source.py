import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, NotificationDelivery, Opportunity
from jobradar.domain.enums import DeliveryStatus, WorkMode
from jobradar.domain.models import RawListing
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import TelegramClient
from jobradar.sources import djinni as djinni_module
from jobradar.sources.base import CachedListing
from jobradar.sources.djinni import MAX_FEED_BYTES, DjinniSource, DjinniSourceError
from jobradar.sources.djinni_rss import description_text
from jobradar.sources.structured_data import parse_job_postings

LEGACY_JOB = {
    "@type": "JobPosting",
    "identifier": 844408,
    "url": "https://djinni.co/jobs/844408-junior-python-developer/",
    "title": "Junior Python Developer",
    "description": "Previous description",
    "jobLocationType": "TELECOMMUTE",
    "hiringOrganization": {"name": "Example Company"},
    "applicantLocationRequirements": {"address": {"addressRegion": "Europe"}},
    "employmentType": "FULL_TIME",
    "estimatedSalary": {"currency": "USD", "minValue": 1200, "maxValue": 1800},
    "datePosted": "2026-08-22T15:36:45+03:00",
}


def _detail(posting: dict) -> str:
    return '<script type="application/ld+json">' + json.dumps(posting) + "</script>"


CZECH_OFFICE_FEED = "https://djinni.co/jobs/rss/?employment=office&country=CZE"


@pytest.mark.asyncio
async def test_additional_office_feed_shares_discovery_and_deduplicates_numeric_ids(
    fast_rss_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        items = (
            (_item(844408), _item(844409))
            if request.url.params.get("employment") == "office"
            else (_item(),)
        )
        return httpx.Response(200, text=_rss(*items))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            client=client, additional_feed_urls=(CZECH_OFFICE_FEED, CZECH_OFFICE_FEED)
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 2 and len(requests) == 2
    assert requests[0]["employment"] == "remote"
    assert requests[1] == {"employment": "office", "country": "CZE"}
    assert source.normalize(rows[0]).work_mode is WorkMode.REMOTE
    assert source.normalize(rows[1]).work_mode is WorkMode.ONSITE
    assert source.normalize(rows[1]).location_text is None
    assert rows[1].payload["rss"]["employment"] == "office"


@pytest.mark.asyncio
async def test_additional_feed_uses_shared_request_budget(fast_rss_requests: None) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(200, text=_rss(_item()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            client=client, additional_feed_urls=(CZECH_OFFICE_FEED,), max_feed_requests=1
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1 and len(requests) == 1
    assert source.consume_run_metrics().limit_reached
    assert source.consume_warnings()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_blocked_additional_feed_stops_all_metadata_requests(
    fast_rss_requests: None,
    status: int,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return (
            httpx.Response(status)
            if request.url.params.get("employment") == "office"
            else httpx.Response(200, text=_rss(_item()))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            client=client, additional_feed_urls=(CZECH_OFFICE_FEED,), metadata_enabled=True
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1 and requests == ["/jobs/rss/", "/jobs/rss/"]
    assert source.consume_run_metrics().detail_failure_count == 0
    assert source.consume_warnings()


@pytest.mark.parametrize("url", ["https://evil.example/jobs/", "http://djinni.co/jobs/"])
def test_additional_feeds_reject_untrusted_urls(url: str) -> None:
    with pytest.raises(ValueError):
        DjinniSource(additional_feed_urls=(url,))


def test_additional_feeds_are_bounded() -> None:
    with pytest.raises(ValueError, match="eight"):
        DjinniSource(
            additional_feed_urls=tuple(f"https://djinni.co/jobs/rss/?x={i}" for i in range(9))
        )


@pytest.mark.asyncio
async def test_additional_office_feed_gets_metadata_before_unchecked_remote_backlog(
    fast_rss_requests: None,
) -> None:
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/rss/":
            return httpx.Response(
                200,
                text=_rss(
                    _item(844409) if request.url.params.get("employment") == "office" else _item()
                ),
            )
        pages.append(request.url.path)
        posting = {
            **LEGACY_JOB,
            "identifier": 844409,
            "url": str(request.url),
            "jobLocationType": None,
            "applicantLocationRequirements": None,
            "jobLocation": {
                "address": {"addressCountry": ["Czechia", "Poland"], "addressLocality": ["Prague"]}
            },
        }
        return httpx.Response(200, text=_detail(posting))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            client=client,
            additional_feed_urls=(CZECH_OFFICE_FEED,),
            metadata_enabled=True,
            max_metadata_requests=1,
        )
        rows = [row async for row in source.fetch()]
    assert len(pages) == 1 and "844409" in pages[0]
    job = source.normalize(rows[1])
    assert job.work_mode is WorkMode.ONSITE
    assert job.location_text == "Czechia, Poland, Prague"
    assert rows[1].detail_fetched_at is not None


@pytest.mark.asyncio
async def test_failed_bump_retry_does_not_starve_successful_expired_cache(
    fast_rss_requests: None,
) -> None:
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/rss/":
            return httpx.Response(200, text=_rss(_item(), _item(844409)))
        pages.append(request.url.path)
        return httpx.Response(404)

    now = datetime.now(UTC)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True, max_metadata_requests=1)
        source.prime_listing_cache(
            {
                "844408": CachedListing(
                    {
                        **LEGACY_JOB,
                        "metadata_attempted_at": now.isoformat(),
                        "metadata_rss_updated_at": "old-bump",
                    },
                    now - timedelta(days=1),
                ),
                "844409": CachedListing(
                    {
                        **LEGACY_JOB,
                        "metadata_attempted_at": (now - timedelta(days=3)).isoformat(),
                        "metadata_rss_updated_at": "2026-08-22T12:36:45+00:00",
                    },
                    now - timedelta(days=2),
                ),
            }
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 2
    assert len(pages) == 1 and "844409" in pages[0]


@pytest.mark.asyncio
async def test_additional_configured_category_failure_is_visible(fast_rss_requests: None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item())))
    ) as client:
        source = DjinniSource(
            client=client, additional_feed_urls=(CZECH_OFFICE_FEED + "&primary_keyword=Java",)
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1
    assert source.consume_run_metrics().limit_reached
    assert any("additional feed" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_detail_metadata_is_cached_and_bumps_do_not_become_publication_dates(
    fast_rss_requests: None,
) -> None:
    requests = []
    date = "Sat, 22 Aug 2026 15:36:45 +0300"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200,
            text=_rss(_item(date=date))
            if request.url.path == "/jobs/rss/"
            else _detail(LEGACY_JOB),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        first = [listing async for listing in source.fetch()]
        normalized = source.normalize(first[0])
        assert normalized.company == "Example Company"
        assert normalized.location_text == "Europe"
        assert normalized.salary_min == Decimal("1200")
        assert normalized.employment_type == "full_time"
        assert normalized.published_at is None
        assert first[0].payload["description"].startswith("Build APIs")
        source.prime_listing_cache(
            {first[0].external_id: CachedListing(first[0].payload, first[0].detail_fetched_at)}
        )
        second = [listing async for listing in source.fetch()]
        assert second == first
        assert len(requests) == 3
        date = "Sun, 23 Aug 2026 15:36:45 +0300"
        third = [listing async for listing in source.fetch()]
        assert len(requests) == 5
        assert source.normalize(third[0]).source_updated_at == datetime(
            2026, 8, 23, 12, 36, 45, tzinfo=UTC
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 429, 503])
async def test_metadata_failure_retains_rss_and_cached_fields_and_stops_when_blocked(
    fast_rss_requests: None,
    status: int,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return (
            httpx.Response(200, text=_rss(_item(), _item(844409)))
            if request.url.path == "/jobs/rss/"
            else httpx.Response(status, headers={"Location": "https://evil.example/private"})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        source.prime_listing_cache({"844408": CachedListing(LEGACY_JOB, None)})
        listings = [listing async for listing in source.fetch()]
        assert len(listings) == 2
        assert source.normalize(listings[0]).company == "Example Company"
        assert listings[0].detail_fetched_at is None
        assert len(requests) == (2 if status in {403, 429} else 3)
        assert source.consume_run_metrics().detail_failure_count == (
            1 if status in {403, 429} else 2
        )
        assert any(f"HTTP {status}" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_metadata_budget_and_persisted_attempt_order_do_not_starve_other_listings(
    fast_rss_requests: None,
) -> None:
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/rss/":
            return httpx.Response(200, text=_rss(_item(), _item(844409)))
        pages.append(request.url.path)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True, max_metadata_requests=1)
        first = [listing async for listing in source.fetch()]
        assert len(first) == 2 and len(pages) == 1
        assert source.consume_run_metrics().metadata_deferred_count == 1
        source.prime_listing_cache(
            {
                listing.external_id: CachedListing(listing.payload, listing.detail_fetched_at)
                for listing in first
            }
        )
        await anext(source.fetch())
        assert len(pages) == 2 and pages[0] != pages[1]


@pytest.mark.asyncio
async def test_exhausted_persistent_metadata_budget_preserves_rss_without_failure(
    fast_rss_requests: None,
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from jobradar.db.models import Source, SourceRun
    from jobradar.domain.enums import RunStatus

    now = datetime.now(UTC).timestamp()
    async with sqlite_session_factory() as session, session.begin():
        session.add(
            Source(
                name="djinni",
                display_name="Djinni",
                request_budget={
                    "metadata": [now - 1000 + index * 2 for index in range(100)],
                },
            )
        )
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, text=_rss(_item(), _item(844409)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await IngestionService(sqlite_session_factory).run_source(
            DjinniSource(client=client, metadata_enabled=True)
        )
    assert paths == ["/jobs/rss/"]
    assert result.status is RunStatus.SUCCEEDED and result.errors == 0
    assert result.metadata_deferred == 2 and result.detail_failures == 0
    async with sqlite_session_factory() as session:
        run = await session.scalar(select(SourceRun))
        assert run is not None and run.metadata_deferred_count == 2
        assert not run.limit_reached and run.warning_count == 0
        assert await session.scalar(select(func.count()).select_from(Listing)) == 2


@pytest.mark.asyncio
async def test_rss_budget_wait_is_bounded_by_run_deadline_without_network_access() -> None:
    class ExhaustedBudget:
        async def reserve(self, category: str) -> bool:
            return False

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, text=_rss(_item()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, run_timeout_seconds=0.02)
        source.configure_request_budget(ExhaustedBudget())
        with pytest.raises(DjinniSourceError, match="timed out"):
            await anext(source.fetch())
    assert calls == []
    assert not source._fetching


@pytest.mark.asyncio
async def test_metadata_cache_expiration_refreshes_the_page(fast_rss_requests: None) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200, text=_rss(_item()) if request.url.path == "/jobs/rss/" else _detail(LEGACY_JOB)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        first = [listing async for listing in source.fetch()]
        source.prime_listing_cache(
            {"844408": CachedListing(first[0].payload, datetime.now(UTC) - timedelta(days=2))}
        )
        second = [listing async for listing in source.fetch()]
        assert len(requests) == 4
        assert second[0].detail_fetched_at is not None
        assert source.consume_run_metrics().detail_failure_count == 0


@pytest.mark.asyncio
async def test_metadata_deadline_does_not_discard_successful_rss(fast_rss_requests: None) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/rss/":
            return httpx.Response(200, text=_rss(_item()))
        await asyncio.sleep(0.1)
        return httpx.Response(200, text=_detail(LEGACY_JOB))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True, request_timeout_seconds=0.02)
        listings = [listing async for listing in source.fetch()]
        assert len(listings) == 1 and listings[0].detail_fetched_at is None
        assert source.consume_run_metrics().detail_failure_count == 1
        assert any(
            "metadata request failed or timed out" in warning
            for warning in source.consume_warnings()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "<html>No JSON-LD</html>",
        _detail({**LEGACY_JOB, "identifier": 1}),
        _detail({**LEGACY_JOB, "url": "https://evil.example/jobs/844408-junior-python-developer/"}),
        "x" * 1_000_001,
    ],
    ids=["missing-jsonld", "wrong-id", "untrusted-url", "oversized"],
)
async def test_invalid_or_oversized_metadata_cannot_replace_cached_fields(
    fast_rss_requests: None,
    body: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_rss(_item()) if request.url.path == "/jobs/rss/" else body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        source.prime_listing_cache({"844408": CachedListing(LEGACY_JOB, None)})
        listing = await anext(source.fetch())
        assert source.normalize(listing).company == "Example Company"
        assert listing.detail_fetched_at is None
        assert source.consume_run_metrics().detail_failure_count == 1


@pytest.mark.asyncio
async def test_successful_metadata_refresh_removes_withdrawn_salary_and_uses_candidate_country(
    fast_rss_requests: None,
) -> None:
    posting = {key: value for key, value in LEGACY_JOB.items() if key != "estimatedSalary"}
    posting["applicantLocationRequirements"] = {"address": {"addressCountry": "UA"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=_rss(_item()) if request.url.path == "/jobs/rss/" else _detail(posting)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        source.prime_listing_cache({"844408": CachedListing(LEGACY_JOB, None)})
        listing = await anext(source.fetch())
        normalized = source.normalize(listing)
        assert normalized.salary_min is None and normalized.location_text == "UA"
        assert listing.detail_fetched_at is not None


@pytest.mark.asyncio
async def test_blocked_rss_does_not_fall_back_to_html(fast_rss_requests: None) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.url.path == "/jobs/rss/"
        return (
            httpx.Response(200, text=_catalog_feed(range(100), catalog=("Python",)))
            if len(requests) == 1
            else httpx.Response(429)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        assert len([listing async for listing in source.fetch()]) == 100
    assert len(requests) == 2


def _item(
    identifier: int = 844408,
    *,
    title: str = "Junior Python Developer",
    url: str | None = None,
    date: str = "Sat, 22 Aug 2026 15:36:45 +0300",
) -> str:
    link = url or f"https://djinni.co/jobs/{identifier}-junior-python-developer/"
    return (
        f"<item><title>{escape(title)}</title><link>{escape(link)}</link>"
        "<description><![CDATA[<p>Build APIs with <strong>Python</strong> and Django.</p>"
        "<p>Remote work in Europe.</p>]]></description>"
        f"<pubDate>{escape(date)}</pubDate><guid>{escape(link)}</guid>"
        "<category>Python</category><category/></item>"
    )


def _rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        + "".join(items)
        + "</channel></rss>"
    )


def _catalog_feed(identifiers, category="Python", catalog=()):  # type: ignore[no-untyped-def]
    items = [
        _item(identifier).replace(
            "<category>Python</category>", f"<category>{escape(category)}</category>"
        )
        for identifier in identifiers
    ]
    return _rss(*items).replace(
        "<channel>",
        "<channel>" + "".join(f"<category>{escape(value)}</category>" for value in catalog),
    )


@pytest.fixture
def fast_rss_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(self):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(djinni_module._RequestLimiter, "wait", no_wait)

    async def allow(category: str) -> bool:
        return True

    monkeypatch.setattr(
        djinni_module.MemoryRequestBudget, "reserve", lambda self, category: allow(category)
    )


@pytest.mark.asyncio
async def test_adaptive_rss_collection_exceeds_200_without_duplicates_or_lost_parent_items(
    fast_rss_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/jobs/rss/"
        assert request.url.params["employment"] == "remote"
        assert request.url.params["editorial"] == "nonhr"
        category = request.url.params.get("primary_keyword")
        experience = request.url.params.get("exp_level")
        if category is None:
            body = _catalog_feed(range(100), catalog=("Python", "JavaScript"))
        elif category == "JavaScript":
            body = _catalog_feed(range(200, 280), category="JavaScript")
        elif experience is None:
            body = _catalog_feed(range(100))
        elif experience == "no_exp":
            body = _catalog_feed(range(80))
        elif experience == "1y":
            body = _catalog_feed(range(80, 160))
        else:
            body = _rss()
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == len({listing.external_id for listing in listings}) == 240
    assert len(requests) == 14
    assert source.consume_warnings() == ()
    metrics = source.consume_run_metrics()
    assert not metrics.limit_reached
    assert metrics.page_count == 14
    assert metrics.candidate_count == 440 and metrics.filtered_count == 200


@pytest.mark.asyncio
async def test_ignored_catalog_heading_is_not_recursively_requested_or_counted_as_coverage(
    fast_rss_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        category = request.url.params.get("primary_keyword")
        if category == "Python":
            return httpx.Response(200, text=_catalog_feed(range(99)))
        return httpx.Response(
            200, text=_catalog_feed([*range(99), 0], catalog=("Development", "Python"))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == 99 and len(requests) == 3
    assert not source.consume_run_metrics().limit_reached
    assert source.consume_warnings() == ()


@pytest.mark.asyncio
async def test_unsplittable_feed_retains_data_and_signals_limited_coverage(
    fast_rss_requests: None,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_catalog_feed(range(100))))
    ) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?primary_keyword=Python&exp_level=3y"
            "&english_level=upper",
            client=client,
        )
        assert len([listing async for listing in source.fetch()]) == 100
    metrics = source.consume_run_metrics()
    assert metrics.limit_reached and metrics.page_count == 1


@pytest.mark.asyncio
async def test_child_feeds_cannot_hide_parent_items_missing_from_their_union(
    fast_rss_requests: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("primary_keyword") is None:
            return httpx.Response(200, text=_catalog_feed(range(100), catalog=("Python",)))
        return httpx.Response(200, text=_catalog_feed(range(50)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        assert len([listing async for listing in source.fetch()]) == 100
    assert source.consume_run_metrics().limit_reached
    assert "50 parent items" in source.consume_warnings()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_upstream_block_or_rate_limit_stops_remaining_partitions_without_retry(
    fast_rss_requests: None,
    status: int,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200, text=_catalog_feed(range(100), catalog=("Python", "JavaScript"))
            )
        return httpx.Response(status, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        assert len([listing async for listing in source.fetch()]) == 100
    assert len(requests) == 2 and source.consume_run_metrics().limit_reached
    assert any(f"HTTP {status}" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_one_failed_partition_does_not_discard_later_valid_partitions(
    fast_rss_requests: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params.get("primary_keyword")
        if category is None:
            return httpx.Response(
                200, text=_catalog_feed(range(100), catalog=("Python", "JavaScript"))
            )
        if category == "Python":
            return httpx.Response(503)
        return httpx.Response(200, text=_catalog_feed(range(200, 220), category="JavaScript"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        assert len([listing async for listing in source.fetch()]) == 120
    assert source.consume_run_metrics().limit_reached
    assert any("HTTP 503" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_request_budget_is_enforced_before_the_next_network_call(
    fast_rss_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=_catalog_feed(range(100), catalog=("Python", "JavaScript")))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, max_feed_requests=1)
        assert len([listing async for listing in source.fetch()]) == 100
    assert len(requests) == 1 and source.consume_run_metrics().limit_reached
    assert any("budget" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_aggregate_byte_budget_stops_the_walk_and_preserves_previous_items(
    fast_rss_requests: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _catalog_feed(range(100), catalog=("Python", "JavaScript"))
    monkeypatch.setattr(djinni_module, "MAX_RUN_BYTES", len(body.encode()) + 10)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        assert len([listing async for listing in source.fetch()]) == 100
    assert len(requests) == 2 and source.consume_run_metrics().limit_reached
    assert any("byte budget" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_run_deadline_is_checked_between_yielded_items(
    fast_rss_requests: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(djinni_module, "monotonic", lambda: clock[0])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item(1), _item(2))))
    ) as client:
        source = DjinniSource(client=client, run_timeout_seconds=1)
        listings = []
        async for listing in source.fetch():
            listings.append(listing)
            clock[0] = 2.0
    assert len(listings) == 1 and source.consume_run_metrics().limit_reached
    assert any("time budget" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_limiter_spaces_requests_and_cannot_be_configured_above_provider_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    starts = []
    monkeypatch.setattr(djinni_module, "monotonic", lambda: clock[0])

    async def advance(seconds):  # type: ignore[no-untyped-def]
        clock[0] += seconds

    monkeypatch.setattr(djinni_module.asyncio, "sleep", advance)
    limiter = djinni_module._RequestLimiter(0.0)
    for _ in range(101):
        await limiter.wait()
        starts.append(clock[0])
    assert starts[-1] >= 60 / 95 * 100 - 0.001
    assert all(sum(start <= value < start + 60 for value in starts) <= 96 for start in starts)


@pytest.mark.parametrize(
    "html, expected",
    [
        (
            "<p>Use <b>React</b>.js &amp; Python.</p><ul><li>One</li><li>Two</li></ul>",
            "Use React.js & Python.\n- One\n- Two",
        ),
        (
            "<p>Actual requirements.</p><script>fake salary</script><style>fake company</style>",
            "Actual requirements.",
        ),
        ("<div>First<br/>Second&nbsp;line</div>", "First\nSecond line"),
    ],
)
def test_description_formatting_preserves_readable_text_without_hidden_code(
    html: str,
    expected: str,
) -> None:
    assert description_text(html) == expected


@pytest.mark.asyncio
async def test_content_encoded_is_preferred_to_summary_and_retained_as_raw_evidence() -> None:
    body = (
        _rss(_item())
        .replace(
            '<rss version="2.0">',
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        )
        .replace(
            "</item>",
            "<content:encoded><![CDATA[<p>Complete requirements.</p>]]></content:encoded></item>",
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert source.normalize(listings[0]).description == "Complete requirements."
    assert listings[0].payload["rss"]["content_encoded"] == "<p>Complete requirements.</p>"


@pytest.mark.asyncio
async def test_repeated_partition_outage_stops_after_three_failed_requests(
    fast_rss_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=_catalog_feed(
                    range(100), catalog=("Python", "JavaScript", "React.js", "Fullstack")
                ),
            )
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        assert len([listing async for listing in source.fetch()]) == 100
    assert len(requests) == 4
    assert source.consume_run_metrics().limit_reached
    assert sum("HTTP 503" in warning for warning in source.consume_warnings()) == 3


@pytest.mark.asyncio
async def test_full_category_and_experience_partition_is_split_by_english_without_widening_filters(
    fast_rss_requests: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["primary_keyword"] == "Python"
        assert request.url.params["exp_level"] == "3y"
        assert request.url.params["region"] == "eu"
        english = request.url.params.get("english_level")
        if english is None:
            body = _catalog_feed(range(100))
        elif english == "upper":
            body = _catalog_feed(range(80))
        elif english == "fluent":
            body = _catalog_feed(range(80, 130))
        else:
            body = _rss()
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?primary_keyword=Python&exp_level=3y&region=eu",
            client=client,
        )
        assert len([listing async for listing in source.fetch()]) == 130
    metrics = source.consume_run_metrics()
    assert metrics.page_count == 9 and not metrics.limit_reached
    assert source.consume_warnings() == ()


@pytest.mark.asyncio
async def test_new_listing_with_blank_description_is_not_ingested_as_complete() -> None:
    body = _rss(_item()).replace(
        "<description><![CDATA[<p>Build APIs with <strong>Python</strong> and Django.</p>"
        "<p>Remote work in Europe.</p>]]></description>",
        "<description/>",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        assert [listing async for listing in source.fetch()] == []
    assert source.consume_run_metrics().filtered_count == 1
    assert "malformed RSS" in source.consume_warnings()[0]


@pytest.mark.asyncio
async def test_ignored_explicit_category_fails_instead_of_silently_widening_search() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_catalog_feed(range(5))))
    ) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?primary_keyword=Development", client=client
        )
        with pytest.raises(DjinniSourceError, match="configured category"):
            _ = [listing async for listing in source.fetch()]


@pytest.mark.asyncio
async def test_overlapping_fetch_is_rejected_and_closed_iterator_releases_adapter(
    fast_rss_requests: None,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_catalog_feed(range(2))))
    ) as client:
        source = DjinniSource(client=client)
        first = source.fetch()
        assert (await anext(first)).external_id == "0"
        with pytest.raises(DjinniSourceError, match="already running"):
            _ = [listing async for listing in source.fetch()]
        await first.aclose()
        assert len([listing async for listing in source.fetch()]) == 2


@pytest.mark.asyncio
async def test_blank_rss_description_preserves_known_text_and_warns() -> None:
    body = _rss(_item()).replace(
        "<description><![CDATA[<p>Build APIs with <strong>Python</strong> and Django.</p>"
        "<p>Remote work in Europe.</p>]]></description>",
        "<description/>",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        source.prime_listing_cache({"844408": CachedListing(LEGACY_JOB, None)})
        listings = [listing async for listing in source.fetch()]
    assert source.normalize(listings[0]).description == "Previous description"
    assert listings[0].payload["description_origin"] == "previously_stored_description"
    assert "stored text was retained" in source.consume_warnings()[0]


def test_parse_job_postings_ignores_unrelated_json_ld() -> None:
    postings = parse_job_postings(
        '<script type="application/ld+json">[{"@type":"WebSite"},'
        '{"@type":"JobPosting","identifier":844408}]</script>'
    )
    assert [posting["identifier"] for posting in postings] == [844408]


@pytest.mark.asyncio
async def test_rss_preserves_identity_and_full_description_without_inventing_metadata() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/jobs/rss/"
        assert request.url.params["employment"] == "remote"
        assert request.url.params["editorial"] == "nonhr"
        assert "application/rss+xml" in request.headers["accept"]
        return httpx.Response(200, text=_rss(_item()))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(requests) == 1
    assert listings[0].external_id == "844408"
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.description == "Build APIs with Python and Django.\nRemote work in Europe."
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.location_text == "Remote"
    assert normalized.company is normalized.salary_min is normalized.salary_max is None
    assert normalized.employment_type is None
    assert normalized.published_at is None
    assert normalized.source_updated_at == datetime(2026, 8, 22, 12, 36, 45, tzinfo=UTC)
    assert listings[0].payload["rss"]["categories"] == ["Python"]
    assert "<strong>" in listings[0].payload["rss"]["description"]
    assert source.deactivate_missing_listings is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://djinni.co/jobs/l-nonhr/remote/?primary_keyword=Python&page=2",
        "https://djinni.co/jobs/rss/?editorial=nonhr&primary_keyword=Python&employment=office",
    ],
)
async def test_legacy_configuration_preserves_filters_and_never_requests_html(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jobs/rss/"
        assert request.url.params["employment"] == "remote"
        assert request.url.params["editorial"] == "nonhr"
        assert request.url.params["primary_keyword"] == "Python"
        assert "page" not in request.url.params
        return httpx.Response(200, text=_rss())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(jobs_url=url, max_pages=20, client=client)
        assert [listing async for listing in source.fetch()] == []
    assert source.consume_run_metrics().page_count == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://djinni.co/jobs/rss/",
        "https://evil.test/jobs/rss/",
        "https://djinni.co/jobs/unrecognized-filter/",
        "https://user:password@djinni.co/jobs/rss/",
    ],
)
def test_feed_url_rejects_untrusted_hosts_and_unsupported_legacy_paths(url: str) -> None:
    with pytest.raises(ValueError):
        DjinniSource(jobs_url=url)


@pytest.mark.asyncio
async def test_repeated_query_filters_are_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list("primary_keyword") == ["Python", "JavaScript"]
        assert request.url.params.get_list("employment") == ["remote"]
        return httpx.Response(200, text=_rss())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?primary_keyword=Python"
            "&primary_keyword=JavaScript&employment=office&employment=parttime",
            client=client,
        )
        assert [listing async for listing in source.fetch()] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "employment, expected",
    [
        ("office", WorkMode.ONSITE),
        ("parttime", WorkMode.UNKNOWN),
        (None, WorkMode.UNKNOWN),
    ],
)
async def test_disabled_remote_filter_does_not_invent_remote_work_mode(
    employment: str | None,
    expected: WorkMode,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item())))
    ) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/"
            + (f"?employment={employment}" if employment is not None else ""),
            remote_only=False,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]
    assert source.normalize(listings[0]).work_mode is expected


@pytest.mark.asyncio
async def test_guid_fallback_and_observed_feed_saturation() -> None:
    body = _rss(
        *(
            _item(identifier).replace(
                f"<link>https://djinni.co/jobs/{identifier}-junior-python-developer/</link>", ""
            )
            for identifier in range(100)
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == 100 and listings[0].external_id == "0"
    assert source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_cached_metadata_survives_migration_and_subsequent_rss_refresh() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item())))
    ) as client:
        source = DjinniSource(client=client)
        payload = LEGACY_JOB
        for _ in range(2):
            source.prime_listing_cache({"844408": CachedListing(payload, None)})
            listings = [listing async for listing in source.fetch()]
            normalized = source.normalize(listings[0])
            assert normalized.company == "Example Company"
            assert normalized.location_text == "Europe"
            assert normalized.salary_min == Decimal("1200")
            assert normalized.salary_max == Decimal("1800")
            assert normalized.salary_currency == "USD"
            assert normalized.employment_type == "full_time"
            assert normalized.description != LEGACY_JOB["description"]
            assert listings[0].payload["metadata_origin"] == "previously_stored_metadata"
            payload = listings[0].payload


@pytest.mark.asyncio
async def test_feed_has_no_local_200_item_ceiling_and_deduplicates_numeric_ids() -> None:
    body = _rss(*(_item(identifier) for identifier in range(1000, 1251)), _item(1000))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == 251
    metrics = source.consume_run_metrics()
    assert metrics.candidate_count == 252
    assert metrics.filtered_count == 1
    assert metrics.limit_reached


@pytest.mark.asyncio
async def test_local_limit_is_observable() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item(1), _item(2))))
    ) as client:
        source = DjinniSource(client=client, max_items=1)
        assert len([listing async for listing in source.fetch()]) == 1
    assert source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_malformed_items_do_not_hide_valid_items() -> None:
    body = _rss(_item(url="https://evil.test/jobs/123/"), _item(title=""), _item(date="invalid"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == 1
    assert source.normalize(listings[0]).published_at is None
    assert source.consume_warnings() == ("Djinni skipped 2 malformed RSS items.",)
    assert source.consume_run_metrics().filtered_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "<html></html>",
        "<rss><channel>",
        '<!DOCTYPE rss [<!ENTITY text "unsafe">]><rss><channel>&text;</channel></rss>',
        '<!DOCTYPE rss SYSTEM "https://example.test/external.dtd"><rss><channel/></rss>',
    ],
)
async def test_invalid_html_and_unsafe_xml_fail_without_fallback(body: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DjinniSource(client=client)
        with pytest.raises(DjinniSourceError, match="RSS"):
            _ = [listing async for listing in source.fetch()]


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.05)
        yield b"<rss><channel/></rss>"


@pytest.mark.asyncio
async def test_whole_response_deadline_is_enforced() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowStream()))
    ) as client:
        source = DjinniSource(client=client, request_timeout_seconds=0.01)
        with pytest.raises(DjinniSourceError, match="timed out"):
            _ = [listing async for listing in source.fetch()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * (MAX_FEED_BYTES + 1)),
        httpx.Response(429),
        httpx.Response(503),
        httpx.Response(302, headers={"location": "https://djinni.co/jobs/"}),
    ],
)
async def test_oversized_responses_http_errors_and_redirects_are_not_silently_accepted(
    response: httpx.Response,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        source = DjinniSource(client=client)
        with pytest.raises(DjinniSourceError):
            _ = [listing async for listing in source.fetch()]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_rss_migration_updates_existing_listing_without_duplicate_or_deactivation(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class LegacySource(DjinniSource):
        async def fetch(self):  # type: ignore[no-untyped-def]
            yield RawListing(external_id="844408", source_url=LEGACY_JOB["url"], payload=LEGACY_JOB)

    service = IngestionService(sqlite_session_factory)
    assert (await service.run_source(LegacySource())).created == 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=_rss(_item())))
    ) as client:
        source = DjinniSource(client=client)
        first = await service.run_source(source)
        second = await service.run_source(source)
    assert first.updated == 1 and first.created == 0 and first.deactivated == 0
    assert second.unchanged == 1 and second.updated == 0 and second.created == 0
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 1
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 1
        opportunity = await session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.company == "Example Company"
        assert (
            opportunity.description == "Build APIs with Python and Django.\nRemote work in Europe."
        )


@pytest.mark.asyncio
async def test_migration_does_not_resend_a_previously_sent_notification(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    fast_rss_requests: None,
) -> None:
    class LegacySource(DjinniSource):
        async def fetch(self):  # type: ignore[no-untyped-def]
            payload = {key: value for key, value in LEGACY_JOB.items() if key != "estimatedSalary"}
            yield RawListing(external_id="844408", source_url=LEGACY_JOB["url"], payload=payload)

    service = IngestionService(sqlite_session_factory)
    await service.run_source(LegacySource())
    matching = MatchingService(sqlite_session_factory)
    await matching.evaluate(BOHDAN_PROFILE)
    async with sqlite_session_factory() as session, session.begin():
        listing = await session.scalar(select(Listing))
        assert listing is not None
        previous_hash = listing.content_hash
        session.add(
            NotificationDelivery(
                opportunity_id=listing.opportunity_id,
                profile_id=BOHDAN_PROFILE.profile_id,
                channel="telegram",
                event_key=f"match:{BOHDAN_PROFILE.rules_version}:{previous_hash[:16]}",
                status=DeliveryStatus.SENT.value,
                sent_at=datetime.now(UTC),
            )
        )

    bump_date = "Sat, 22 Aug 2026 15:36:45 +0300"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "djinni.co"
        return httpx.Response(
            200,
            text=_rss(_item(date=bump_date))
            if request.url.path == "/jobs/rss/"
            else _detail(
                {key: value for key, value in LEGACY_JOB.items() if key != "estimatedSalary"}
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        await service.run_source(source)
        assert (await matching.evaluate(BOHDAN_PROFILE)).evaluated == 1
        notifications = NotificationService(
            sqlite_session_factory, TelegramClient("synthetic-token", 1, client=client)
        )
        summary = await notifications.dispatch(
            BOHDAN_PROFILE, minimum_score=0, max_messages=10, minimum_first_seen_at=None
        )
        bump_date = "Sun, 23 Aug 2026 15:36:45 +0300"
        bumped = await service.run_source(source)
        assert bumped.created == 0 and bumped.updated == 1
        assert (await matching.evaluate(BOHDAN_PROFILE)).evaluated == 1
        repeated = await notifications.dispatch(
            BOHDAN_PROFILE, minimum_score=0, max_messages=10, minimum_first_seen_at=None
        )
        assert repeated.sent == 0 and repeated.failed == 0 and repeated.skipped_duplicate == 1
    assert summary.sent == 0 and summary.failed == 0 and summary.skipped_duplicate == 1
    async with sqlite_session_factory() as session:
        listing = await session.scalar(select(Listing))
        assert listing is not None and listing.content_hash != previous_hash
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 1
