from datetime import UTC, datetime
from xml.sax.saxutils import escape

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.sources.base import CachedListing
from jobradar.sources.dou_jobs import DouJobsSource


def rss(*items: tuple[str, str, str]) -> str:
    return (
        "<rss><channel>"
        + "".join(
            f"<item><title>{escape(title)}</title>"
            f"<link>https://jobs.dou.ua/companies/example/vacancies/{identifier}/</link>"
            f"<description>{escape(description)}</description></item>"
            for identifier, title, description in items
        )
        + "</channel></rss>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location,description,expected",
    [
        ("Prague", "Full-time office development", WorkMode.ONSITE),
        ("Praha, hybrid", "Full-time development", WorkMode.HYBRID),
        ("Prague, remote", "Hybrid work, two days in the office", WorkMode.HYBRID),
        ("Kyiv", "Our company also has an office in Prague. Hybrid work.", None),
        ("Berlin", "Full-time office development", None),
        ("Remote", "Full-time remote development", WorkMode.REMOTE),
    ],
)
async def test_dou_retains_positive_prague_work_modes_not_company_location(
    location, description, expected
) -> None:
    body = rss(("810001", f"Junior Developer в Example, {location}", description))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        source = DouJobsSource(client=client, local_city_aliases=BOHDAN_PROFILE.local_city_aliases)
        rows = [row async for row in source.fetch()]
    if expected is None:
        assert rows == []
    else:
        assert len(rows) == 1
        assert source.normalize(rows[0]).work_mode is expected


@pytest.mark.asyncio
async def test_multiple_feeds_deduplicate_and_new_records_are_not_crowded_out_by_cache() -> None:
    async def handler(request):
        return httpx.Response(
            200,
            text=rss(
                ("810001", "Junior Developer в Example, remote", "React remote development"),
                *(
                    (
                        "810002",
                        "Junior Next.js Developer в Other, remote",
                        "Next.js remote development",
                    ),
                )
                if request.url.params.get("search")
                else (),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DouJobsSource(
            client=client,
            max_items=1,
            additional_feed_urls=("https://jobs.dou.ua/vacancies/feeds/?remote=&search=next.js",),
        )
        source.prime_listing_cache({"810001": CachedListing({}, datetime.now(UTC))})
        rows = [row async for row in source.fetch()]
    assert [row.external_id for row in rows] == ["810002"]
    metrics = source.consume_run_metrics()
    assert metrics.page_count == 2 and metrics.candidate_count == 3 and metrics.limit_reached


@pytest.mark.asyncio
async def test_one_feed_failure_does_not_discard_other_feeds_and_remains_observable() -> None:
    def handler(request):
        if not request.url.params.get("search"):
            return httpx.Response(502)
        return httpx.Response(
            200, text=rss(("810002", "Junior Developer в Example, remote", "React"))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DouJobsSource(
            client=client,
            additional_feed_urls=("https://jobs.dou.ua/vacancies/feeds/?remote=&search=react",),
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1
    assert source.consume_warnings() == ("DOU Jobs RSS request failed (HTTPStatusError).",)


@pytest.mark.asyncio
async def test_feed_rotation_caps_network_calls_and_keeps_primary_and_prague_feeds() -> None:
    urls = tuple(f"https://jobs.dou.ua/vacancies/feeds/?search=q{i}" for i in range(13))
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or httpx.Response(200, text=rss())
        )
    ) as client:
        for index in range(2):
            source = DouJobsSource(
                feed_url=urls[0], additional_feed_urls=urls[1:], client=client, max_feeds_per_run=8
            )
            source.configure_discovery_run(index)
            assert [row async for row in source.fetch()] == []
    assert len(calls) == 16 and set(calls) == set(urls)
    assert calls[:2] == calls[8:10] == list(urls[:2])


@pytest.mark.asyncio
async def test_feed_wait_cannot_overrun_the_collection_deadline(monkeypatch) -> None:
    class ExpiredBudget:
        def __init__(self, deadline):
            self.deadline = 0
            self.consumed_bytes = 0
            self.maximum_bytes = 100

    monkeypatch.setattr("jobradar.sources.dou_jobs.DownloadBudget", ExpiredBudget)
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, text=rss())
        )
    ) as client:
        source = DouJobsSource(client=client)
        with pytest.raises(RuntimeError, match="Every configured"):
            _ = [row async for row in source.fetch()]
    assert calls == []
    assert "run budget" in source.consume_warnings()[0]
