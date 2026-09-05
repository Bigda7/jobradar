from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.base import CachedListing
from jobradar.sources.workua import (
    WorkUaSource,
    parse_salary,
    parse_workua_cards,
    parse_workua_description,
)

SEARCH_PAGE = """
<html><body>
<a name="8441545"></a>
<div class="card card-hover wordwrap job-link">
  <div><h2><a href="/en/jobs/8441545/">Backend Developer (Python, Django)</a></h2></div>
  <div><div><span title="Salary"></span>
    <span class="strong-600">55 000 - 60 000 UAH</span></div></div>
  <div><span title="Company Information"></span><span>
    <span class="strong-600">Example Labs</span></span><span>, Remote</span></div>
  <p class="ellipsis ellipsis-line">
    Full-time. We are also ready to hire a student. Build Django APIs.</p>
  <div><time datetime="2026-08-21 15:27:47">yesterday</time></div>
</div>
<a name="8441546"></a>
<div class="card card-hover wordwrap job-link">
  <div><h2><a href="/en/jobs/8441546/">Office Developer</a></h2></div>
  <div><span title="Company Information"></span><span>
    <span class="strong-600">Office Corp</span></span><span>, Kyiv</span></div>
  <p class="ellipsis ellipsis-line">Full-time. Office only.</p>
</div>
</body></html>
"""

DETAIL_PAGE = """
<html><body>
<div>Unrelated content</div>
<div id="job-description" class="company-description">
  <h2>About the role</h2>
  <p>Build production Django APIs and React interfaces.</p>
  <ul><li>Write tests</li><li>Review code</li></ul>
</div>
<div>Unrelated footer</div>
</body></html>
"""


def test_workua_html_parser_extracts_cards() -> None:
    cards = parse_workua_cards(SEARCH_PAGE)

    assert len(cards) == 2
    assert cards[0].external_id == "8441545"
    assert cards[0].company == "Example Labs"
    assert cards[0].salary_text == "55 000 - 60 000 UAH"
    assert cards[0].location_text == "Remote"


def test_workua_salary_parser_handles_grouped_uah_range() -> None:
    assert parse_salary("55 000 - 60 000 UAH") == (
        Decimal("55000"),
        Decimal("60000"),
        "UAH",
    )


def test_workua_detail_parser_extracts_full_description() -> None:
    description = parse_workua_description(DETAIL_PAGE)

    assert description is not None
    assert "Build production Django APIs" in description
    assert "Write tests" in description
    assert "Unrelated footer" not in description


@pytest.mark.asyncio
async def test_workua_source_keeps_remote_jobs_and_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Return-Format"] == "html"
        if request.url == httpx.URL("https://reader.test/en/jobs-remote-python/"):
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url == httpx.URL("https://reader.test/en/jobs/8441545/"):
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert str(listings[0].source_url) == "https://www.work.ua/en/jobs/8441545/"
    normalized = source.normalize(listings[0])
    assert normalized.title == "Backend Developer (Python, Django)"
    assert normalized.company == "Example Labs"
    assert normalized.description is not None
    assert "Build production Django APIs" in normalized.description
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("55000")
    assert normalized.salary_max == Decimal("60000")
    assert normalized.salary_currency == "UAH"
    assert normalized.salary_period == "month"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-21T15:27:47+00:00"


@pytest.mark.asyncio
async def test_workua_source_reuses_cached_detail_for_an_unchanged_card() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/en/jobs/8441545/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        first = [listing async for listing in source.fetch()]
        source.prime_listing_cache(
            {
                first[0].external_id: CachedListing(
                    payload=first[0].payload,
                    detail_fetched_at=first[0].detail_fetched_at,
                )
            }
        )
        second = [listing async for listing in source.fetch()]

    assert len(first) == len(second) == 1
    assert requested_paths.count("/en/jobs-remote-python/") == 2
    assert requested_paths.count("/en/jobs/8441545/") == 1
    assert second[0].payload["description"] == first[0].payload["description"]
    assert second[0].detail_fetched_at == first[0].detail_fetched_at


@pytest.mark.asyncio
async def test_workua_source_retries_one_rate_limited_detail_request() -> None:
    detail_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_attempts
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/en/jobs/8441545/":
            detail_attempts += 1
            if detail_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            retry_attempts=2,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert detail_attempts == 2


@pytest.mark.asyncio
async def test_workua_source_continues_when_one_search_page_is_empty() -> None:
    empty_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal empty_attempts
        if request.url.path == "/en/jobs-remote-empty/":
            empty_attempts += 1
            return httpx.Response(200, text="<html><body>No jobs</body></html>")
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/en/jobs/8441545/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=(
                "https://www.work.ua/en/jobs-remote-empty/",
                "https://www.work.ua/en/jobs-remote-python/",
            ),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            retry_attempts=2,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert empty_attempts == 2
    assert source.consume_warnings() == ()


@pytest.mark.asyncio
async def test_workua_source_paginates_and_reports_coverage_metrics() -> None:
    second_page = (
        SEARCH_PAGE.replace("8441545", "8441550")
        .replace("Office Developer", "Remote React Developer")
        .replace("Office Corp", "Remote Corp")
        .replace(", Kyiv", ", Remote")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/en/jobs-remote-programmer/":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, text=second_page)
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path in {"/en/jobs/8441545/", "/en/jobs/8441550/"}:
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-programmer/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=2,
            max_items=2,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    metrics = source.consume_run_metrics()
    assert [listing.external_id for listing in listings] == ["8441545", "8441550"]
    assert metrics.page_count == 2
    assert metrics.candidate_count == 4
    assert metrics.filtered_count == 1
    assert metrics.detail_failure_count == 0
    assert metrics.limit_reached is True
