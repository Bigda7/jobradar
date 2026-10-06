import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from structlog.testing import capture_logs

from jobradar.domain.enums import WorkMode
from jobradar.sources import workua as workua_module
from jobradar.sources.base import CachedListing
from jobradar.sources.workua import (
    WorkUaSource,
    WorkUaSourceError,
    is_workua_challenge,
    parse_salary,
    parse_workua_cards,
    parse_workua_description,
    parse_workua_markdown_cards,
    parse_workua_markdown_description,
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

CHALLENGE_PAGE = """
<html><body><script src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform"></script>
Work.ua має перевірити безпеку вашого з'єднання.</body></html>
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["rate_limit", "retry_after", "deadline", "request_budget"])
async def test_shared_stop_preserves_discoveries_without_more_requests_or_detail_waits(
    monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    requested_paths: list[str] = []
    delays: list[float] = []

    async def pause(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/en/jobs-remote-python/":
            if stop == "deadline":
                source._run_deadline = 0.0
            elif stop == "request_budget":
                source._network_request_limit = source._network_requests
            return httpx.Response(200, text=SEARCH_PAGE.replace(", Kyiv", ", Remote"))
        return httpx.Response(
            429 if stop == "rate_limit" else 503,
            headers={"Retry-After": "0" if stop == "rate_limit" else "60"},
        )

    fetched_at = datetime.now(UTC) - timedelta(days=2)
    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = WorkUaSource(
                search_urls=tuple(
                    f"https://www.work.ua/en/jobs-remote-{term}/"
                    for term in ("python", "django", "react")
                ),
                reader_base_url="https://reader.test",
                max_pages_per_search=1,
                detail_request_delay_seconds=1.5,
                client=client,
            )
            source.prime_listing_cache(
                {
                    "8441545": CachedListing(
                        payload={"description": "Previously saved full description"},
                        detail_fetched_at=fetched_at,
                    )
                }
            )
            rows = [row async for row in source.fetch()]
    expected_paths = ["/en/jobs-remote-python/"]
    if stop == "rate_limit":
        expected_paths += ["/en/jobs-remote-django/"] * 2
    elif stop == "retry_after":
        expected_paths += ["/en/jobs-remote-django/"]
    assert requested_paths == expected_paths
    assert delays == ([0.0] if stop == "rate_limit" else [])
    assert len(rows) == 2
    assert rows[0].payload["description"] == "Previously saved full description"
    assert rows[0].payload["detail_status"] == "cached"
    assert rows[0].detail_fetched_at == fetched_at
    assert rows[1].payload["description"] == rows[1].payload["summary"]
    assert rows[1].payload["detail_status"] == "summary"
    assert rows[1].detail_fetched_at is None
    assert source.consume_run_metrics().detail_failure_count == 2
    assert len(source.consume_warnings()) == 3
    events = [log for log in logs if log["event"] == "workua_run_requests_stopped"]
    assert len(events) == 1
    assert events[0]["reason"] == stop
    assert events[0]["network_requests"] == len(expected_paths)


@pytest.mark.asyncio
async def test_rate_limited_run_with_no_cards_fails_once_then_next_cycle_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    blocked = True

    async def pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if blocked:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200, text=SEARCH_PAGE if "jobs-remote" in request.url.path else DETAIL_PAGE
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=(
                "https://www.work.ua/en/jobs-remote-python/",
                "https://www.work.ua/en/jobs-remote-django/",
            ),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        with pytest.raises(WorkUaSourceError) as failure:
            _ = [row async for row in source.fetch()]
        assert failure.value.status_code == 429
        assert requests == ["/en/jobs-remote-python/"] * 2
        assert len(source.consume_warnings()) == 1
        source.begin_run()
        blocked = False
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1 and rows[0].payload["detail_status"] == "complete"
    assert source.consume_warnings() == ()
    assert requests[2:] == [
        "/en/jobs-remote-python/",
        "/en/jobs-remote-django/",
        "/en/jobs/8441545/",
    ]


@pytest.mark.asyncio
async def test_detail_pacing_cannot_wait_past_the_remaining_run_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []
    delays: list[float] = []

    async def pause(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        source._run_deadline = workua_module.monotonic() + 0.5
        return httpx.Response(200, text=SEARCH_PAGE.replace(", Kyiv", ", Remote"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            detail_request_delay_seconds=1.5,
            client=client,
        )
        rows = [row async for row in source.fetch()]
    assert requested_paths == ["/en/jobs-remote-python/"]
    assert delays == [] and len(rows) == 2
    assert all(row.payload["detail_error"] == "deadline" for row in rows)
    assert source.consume_run_metrics().detail_failure_count == 2


@pytest.mark.asyncio
async def test_detail_rate_limit_preserves_other_cards_and_recovers_on_next_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    blocked = True

    async def pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if "jobs-remote" in request.url.path:
            return httpx.Response(200, text=SEARCH_PAGE.replace(", Kyiv", ", Remote"))
        if blocked:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text=DETAIL_PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        rows = [row async for row in source.fetch()]
        assert len(rows) == 2 and all(row.payload["detail_status"] == "summary" for row in rows)
        assert requests == ["/en/jobs-remote-python/", "/en/jobs/8441545/", "/en/jobs/8441545/"]
        assert source.consume_run_metrics().detail_failure_count == 2
        assert len(source.consume_warnings()) == 2
        source.begin_run()
        blocked = False
        recovered = [row async for row in source.fetch()]
    assert len(recovered) == 2
    assert all(row.payload["detail_status"] == "complete" for row in recovered)
    assert (
        source.consume_run_metrics().detail_failure_count == 0 and source.consume_warnings() == ()
    )
    assert requests[3:] == ["/en/jobs-remote-python/", "/en/jobs/8441545/", "/en/jobs/8441546/"]


@pytest.mark.asyncio
async def test_shared_stop_does_not_mark_a_reusable_fresh_description_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []
    blocked = False

    async def pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", pause)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if blocked and request.url.path == "/en/jobs-remote-django/":
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200, text=SEARCH_PAGE if "jobs-remote" in request.url.path else DETAIL_PAGE
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=(
                "https://www.work.ua/en/jobs-remote-python/",
                "https://www.work.ua/en/jobs-remote-django/",
            ),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        first = [row async for row in source.fetch()]
        source.prime_listing_cache(
            {
                first[0].external_id: CachedListing(
                    payload=first[0].payload,
                    detail_fetched_at=first[0].detail_fetched_at,
                )
            }
        )
        source.begin_run()
        requests.clear()
        blocked = True
        rows = [row async for row in source.fetch()]
    assert len(rows) == 1 and rows[0].payload["detail_status"] == "complete"
    assert rows[0].payload["detail_error"] is None
    assert rows[0].detail_fetched_at == first[0].detail_fetched_at
    assert requests == [
        "/en/jobs-remote-python/",
        "/en/jobs-remote-django/",
        "/en/jobs-remote-django/",
    ]
    assert source.consume_run_metrics().detail_failure_count == 0
    assert len(source.consume_warnings()) == 1


MARKDOWN_SEARCH_PAGE = (
    "\n## [Backend Developer (Python, Django)]"
    "(http://www.work.ua/en/jobs/8441545/ "
    '"Backend Developer (Python, Django), job from August 21, 2026")'
    """

55 000 - 60 000 UAH

Example Labs, Agency

Remote

Experience more than 1 year · Full-time

Build Django APIs and React interfaces.

To save a job, you need to sign in.
"""
)

MARKDOWN_DETAIL_PAGE = """
# Backend Developer (Python, Django)

## About the job

Build production **Django APIs** and React interfaces.

* Write tests
* Review code

### Key requirements and skills

Python
"""


def test_workua_html_parser_extracts_cards() -> None:
    cards = parse_workua_cards(SEARCH_PAGE)

    assert len(cards) == 2
    assert cards[0].external_id == "8441545"
    assert cards[0].company == "Example Labs"
    assert cards[0].salary_text == "55 000 - 60 000 UAH"
    assert cards[0].location_text == "Remote"


@pytest.mark.asyncio
async def test_workua_rejects_external_card_link_before_detail_request() -> None:
    search_page = SEARCH_PAGE.replace(
        'href="/en/jobs/8441545/"',
        'href="https://www.work.ua.evil.example/en/jobs/8441545/"',
    )
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text=search_page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert listings == []
    assert requested_paths == ["/en/jobs-remote-python/"]
    assert source.consume_warnings() == ("Work.ua skipped vacancy 8441545 with an unexpected URL.",)


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


def test_workua_markdown_parsers_extract_current_reader_content() -> None:
    cards = parse_workua_markdown_cards(MARKDOWN_SEARCH_PAGE)
    description = parse_workua_markdown_description(MARKDOWN_DETAIL_PAGE)

    assert len(cards) == 1
    assert cards[0].external_id == "8441545"
    assert cards[0].url == "https://www.work.ua/en/jobs/8441545/"
    assert cards[0].company == "Example Labs"
    assert cards[0].location_text == "Remote"
    assert cards[0].published_at == "2026-08-21T00:00:00+00:00"
    assert description is not None
    assert "Build production Django APIs" in description
    assert "Key requirements" not in description


def test_workua_challenge_is_detected() -> None:
    assert is_workua_challenge(CHALLENGE_PAGE) is True
    assert is_workua_challenge(SEARCH_PAGE) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("page_kind", ["search", "detail"])
@pytest.mark.parametrize("response_format", ["html", "markdown"])
@pytest.mark.parametrize(
    "marker",
    [
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
        '<script>const setting = "cf-chl-example";</script>',
        '<script>const label = "performing security verification";</script>',
        "<!-- performing security verification -->",
    ],
)
async def test_valid_listing_content_is_not_discarded_for_incidental_markers(
    page_kind: str, response_format: str, marker: str
) -> None:
    requests: list[str] = []
    fixtures = {
        ("search", "html"): SEARCH_PAGE,
        ("search", "markdown"): MARKDOWN_SEARCH_PAGE,
        ("detail", "html"): DETAIL_PAGE,
        ("detail", "markdown"): MARKDOWN_DETAIL_PAGE,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requested_format = request.headers["X-Return-Format"]
        requests.append(requested_format)
        if requested_format != response_format:
            return httpx.Response(200, text=CHALLENGE_PAGE)
        return httpx.Response(200, text=fixtures[page_kind, response_format] + marker)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = WorkUaSource(client=client)
            if page_kind == "search":
                cards = await source._fetch_search_cards(
                    "https://www.work.ua/en/jobs-remote-python/"
                )
                assert cards[0].external_id == "8441545"
            else:
                description = await source._fetch_description(
                    "https://www.work.ua/en/jobs/8441545/"
                )
                assert description is not None and "Build production Django APIs" in description

    assert requests == (["html"] if response_format == "html" else ["html", "markdown"])
    assert source.consume_warnings() == ()
    assert logs[-1] == {
        "event": "workua_response_classified",
        "log_level": "info",
        "page_kind": page_kind,
        "response_format": response_format,
        "classification": "content",
        "parsed_items": 2 if (page_kind, response_format) == ("search", "html") else 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("page_kind", ["search", "detail"])
@pytest.mark.parametrize("response_format", ["html", "markdown"])
@pytest.mark.parametrize(
    "security_text",
    [
        "Performing security verification",
        "Work.ua має перевірити безпеку вашого з'єднання.",
        "<p>Performing <span>security</span> verification</p>",
        "<p>Performing&nbsp;security&#32;verification</p>",
    ],
)
async def test_explicit_security_text_is_rejected_even_with_listing_shaped_content(
    page_kind: str, response_format: str, security_text: str
) -> None:
    fixtures = {
        ("search", "html"): SEARCH_PAGE,
        ("search", "markdown"): MARKDOWN_SEARCH_PAGE,
        ("detail", "html"): DETAIL_PAGE,
        ("detail", "markdown"): MARKDOWN_DETAIL_PAGE,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["X-Return-Format"] != response_format:
            return httpx.Response(200, text=CHALLENGE_PAGE)
        return httpx.Response(200, text=security_text + fixtures[page_kind, response_format])

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = WorkUaSource(client=client)
            with pytest.raises(WorkUaSourceError) as failure:
                if page_kind == "search":
                    await source._fetch_search_cards("https://www.work.ua/en/jobs-remote-python/")
                else:
                    await source._fetch_description("https://www.work.ua/en/jobs/8441545/")
    assert failure.value.reason == "challenge"
    assert all(log["classification"] == "challenge" for log in logs)


@pytest.mark.parametrize(
    "content",
    [
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
        '<div id="job-description"></div><script>cf-chl-example</script>',
        '<div class="job-link"><a href="/en/jobs/1/"></a></div><script>cf-chl-example</script>',
        '<template><div id="job-description">Fake description</div></template>cf-chl-example',
    ],
)
def test_cloudflare_marker_without_listing_content_remains_a_challenge(content: str) -> None:
    assert parse_workua_cards(content) == []
    assert parse_workua_description(content) is None
    assert is_workua_challenge(content) is True


@pytest.mark.parametrize("hidden_tag", ["script", "style", "template"])
def test_non_content_elements_do_not_pollute_listing_text(hidden_tag: str) -> None:
    hidden = f"<{hidden_tag}>performing security verification</{hidden_tag}>"
    search = SEARCH_PAGE.replace("Build Django APIs.", "Build Django APIs." + hidden)
    detail = DETAIL_PAGE.replace("Write tests", "Write tests" + hidden)
    assert parse_workua_cards(search) == parse_workua_cards(SEARCH_PAGE)
    assert parse_workua_description(detail) == parse_workua_description(DETAIL_PAGE)
    assert is_workua_challenge(search, has_listing_content=True) is False
    assert is_workua_challenge(detail, has_listing_content=True) is False


def test_self_closing_hidden_elements_do_not_hide_valid_listing_content() -> None:
    hidden = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"/>'
    assert parse_workua_cards(hidden + SEARCH_PAGE) == parse_workua_cards(SEARCH_PAGE)
    assert parse_workua_description(hidden + DETAIL_PAGE) == parse_workua_description(DETAIL_PAGE)


def test_response_classification_does_not_log_content_or_request_credentials() -> None:
    secret = "synthetic-secret-that-must-not-be-logged"
    with capture_logs() as logs:
        assert (
            WorkUaSource._response_is_challenge(
                CHALLENGE_PAGE + f'<input value="{secret}">', "search", "html", 0
            )
            is True
        )
        assert WorkUaSource._response_is_challenge("No vacancies", "search", "html", 0) is False
    assert [log["classification"] for log in logs] == ["challenge", "empty"]
    assert secret not in str(logs)
    assert "cloudflare.com" not in str(logs)


@pytest.mark.asyncio
async def test_untrusted_cards_do_not_override_challenge_detection() -> None:
    search = SEARCH_PAGE.replace("/en/jobs/", "https://untrusted.test/en/jobs/")
    search += '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=search))
    ) as client:
        source = WorkUaSource(client=client)
        with pytest.raises(WorkUaSourceError) as failure:
            await source._fetch_search_cards("https://www.work.ua/en/jobs-remote-python/")
    assert failure.value.reason == "challenge"


@pytest.mark.asyncio
async def test_workua_source_falls_back_to_uncached_markdown_after_challenge() -> None:
    requests: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response_format = request.headers["X-Return-Format"]
        no_cache = request.headers.get("X-No-Cache")
        requests.append((request.url.path, response_format, no_cache))
        if response_format == "html":
            return httpx.Response(200, text=CHALLENGE_PAGE)
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=MARKDOWN_SEARCH_PAGE)
        if request.url.path == "/en/jobs/8441545/":
            return httpx.Response(200, text=MARKDOWN_DETAIL_PAGE)
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
    assert listings[0].payload["company"] == "Example Labs"
    assert "Build production Django APIs" in listings[0].payload["description"]
    assert (
        "/en/jobs-remote-python/",
        "markdown",
        "true",
    ) in requests
    assert ("/en/jobs/8441545/", "markdown", "true") in requests


@pytest.mark.asyncio
async def test_workua_rejects_external_markdown_link_before_detail_request() -> None:
    search_page = MARKDOWN_SEARCH_PAGE.replace(
        "http://www.work.ua/en/jobs/8441545/",
        "https://www.work.ua.evil.example/en/jobs/8441545/",
    )
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.headers["X-Return-Format"] == "html":
            return httpx.Response(200, text=CHALLENGE_PAGE)
        return httpx.Response(200, text=search_page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        with pytest.raises(WorkUaSourceError, match="no vacancy cards"):
            _ = [listing async for listing in source.fetch()]

    assert requested_paths == ["/en/jobs-remote-python/", "/en/jobs-remote-python/"]


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
async def test_workua_source_keeps_card_when_detail_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/en/jobs/8441545/":
            return httpx.Response(200, text=CHALLENGE_PAGE)
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
    assert listings[0].external_id == "8441545"
    assert listings[0].payload["description"] == (
        "Full-time. We are also ready to hire a student. Build Django APIs."
    )
    assert listings[0].detail_fetched_at is None
    assert listings[0].payload["detail_status"] == "summary"
    assert source.consume_run_metrics().detail_failure_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["challenge", "missing", "404", "410", "503", "timeout"])
async def test_failed_refresh_preserves_full_text_and_retries_next_cycle(failure: str) -> None:
    detail_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_requests
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        detail_requests += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("Timed out", request=request)
        if failure.isdigit():
            return httpx.Response(int(failure))
        return httpx.Response(200, text=CHALLENGE_PAGE if failure == "challenge" else "No details")

    fetched_at = datetime.now(UTC) - timedelta(days=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        source.prime_listing_cache(
            {
                "8441545": CachedListing(
                    payload={"description": "Previously saved full description"},
                    detail_fetched_at=fetched_at,
                )
            }
        )
        first = [listing async for listing in source.fetch()]
        assert first[0].payload["description"] == "Previously saved full description"
        assert first[0].payload["detail_status"] == "cached"
        assert first[0].detail_fetched_at == fetched_at
        source.prime_listing_cache(
            {
                "8441545": CachedListing(
                    payload=first[0].payload, detail_fetched_at=datetime.now(UTC)
                )
            }
        )
        previous_requests = detail_requests
        second = [listing async for listing in source.fetch()]
        assert detail_requests > previous_requests
        assert second[0].payload["description"] == first[0].payload["description"]
        assert source.consume_run_metrics().detail_failure_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "summary", "cached"])
async def test_summary_with_old_success_timestamp_is_not_reused_as_full_detail(
    status: str | None,
) -> None:
    detail_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal detail_requests
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        detail_requests += 1
        return httpx.Response(200, text=DETAIL_PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=("https://www.work.ua/en/jobs-remote-python/",),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        first = [listing async for listing in source.fetch()]
        payload = {**first[0].payload, "description": first[0].payload["summary"]}
        if status is None:
            payload.pop("detail_status")
        else:
            payload["detail_status"] = status
        source.prime_listing_cache(
            {"8441545": CachedListing(payload=payload, detail_fetched_at=datetime.now(UTC))}
        )
        second = [listing async for listing in source.fetch()]

    assert detail_requests == 2
    assert second[0].payload["detail_status"] == "complete"
    assert "Write tests" in second[0].payload["description"]


@pytest.mark.asyncio
async def test_http_status_is_not_inferred_from_vacancy_id() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        source = WorkUaSource(reader_base_url="https://reader.test", client=client)
        with pytest.raises(WorkUaSourceError) as failure:
            await source._fetch_description("https://www.work.ua/en/jobs/404001/")
    assert failure.value.status_code == 503


@pytest.mark.asyncio
async def test_unavailable_search_is_reported_without_discarding_other_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/en/jobs-remote-blocked/":
            return httpx.Response(200, text=CHALLENGE_PAGE)
        if request.url.path == "/en/jobs-remote-python/":
            return httpx.Response(200, text=SEARCH_PAGE)
        return httpx.Response(200, text=DETAIL_PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = WorkUaSource(
            search_urls=(
                "https://www.work.ua/en/jobs-remote-blocked/",
                "https://www.work.ua/en/jobs-remote-python/",
            ),
            reader_base_url="https://reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]
    assert len(listings) == 1
    assert source.consume_warnings() == (
        "Work.ua search page unavailable (challenge): https://www.work.ua/en/jobs-remote-blocked/.",
    )


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
    assert empty_attempts == 3
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
