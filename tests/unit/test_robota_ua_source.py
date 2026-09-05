import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import WorkMode
from jobradar.domain.normalization import build_content_hash
from jobradar.sources.base import CachedListing
from jobradar.sources.registry import build_source_registry
from jobradar.sources.robota_ua import (
    RobotaUaSource,
    parse_robota_ua_api_detail,
    parse_robota_ua_cards,
    parse_robota_ua_detail,
    parse_robota_ua_salary,
)

SEARCH_PAGE = """
Title: Developer remote jobs

Markdown Content:
[Віддалена робота ## Junior Python Developer 40 000 — 60 000 ₴ Example Labs Київ
(віддалено) Build APIs.](http://robota.ua/company123/vacancy111)
[Віддалена робота ## React Developer Remote Corp Львів (віддалено)
![Image: Remote Corp](https://images.example/logo.png) Build interfaces.]
(https://www.robota.ua/company456/vacancy222?ref=search)
## [Office Developer Office Corp Київ](http://robota.ua/company789/vacancy333)
"""

DETAIL_PAGE = """
Title: Robota.ua

URL Source: http://robota.ua/company123/vacancy111

Markdown Content:
Віддалена робота

# Junior Python Developer

2 години тому

05 вересня 2026

40 000 — 60 000 ₴

Київ

[Example Labs](http://robota.ua/company123)

Recruiter Name

Віддалена робота

Повна зайнятість

Створіть резюме, щоб оцінити свої шанси на вакансію

Система порівняє ваше резюме із вакансією.

Створити резюме

We build production Python services.

**Requirements:**

* FastAPI and PostgreSQL
* React is a plus

Recruiter Name

Показати контакти

Відгукнутись

## [Example Labs](http://robota.ua/company123)
"""


def api_detail_response(
    external_id: str,
    *,
    active: bool = True,
    title: str = "Junior Python Developer",
) -> str:
    payload = {
        "id": int(external_id),
        "name": title,
        "companyName": "Example Labs",
        "description": (
            "<p>We build production Python services.</p>"
            "<p><strong>Requirements:</strong> FastAPI and PostgreSQL.</p>"
        ),
        "cityName": "Київ",
        "date": "2026-09-05T10:15:30.123",
        "salary": 0,
        "salaryFrom": 40000,
        "salaryTo": 60000,
        "salaryComment": "",
        "scheduleId": 1,
        "isActive": active,
    }
    return f"Title:\n\nMarkdown Content:\n{json.dumps(payload, ensure_ascii=False)}"


def test_robota_ua_markdown_parser_extracts_and_canonicalizes_cards() -> None:
    cards = parse_robota_ua_cards(SEARCH_PAGE)

    assert [card.external_id for card in cards] == ["111", "222", "333"]
    assert cards[0].is_remote is True
    assert cards[1].is_remote is True
    assert cards[1].url == "https://robota.ua/company456/vacancy222"
    assert cards[2].is_remote is False


def test_robota_ua_detail_parser_extracts_normalized_fields() -> None:
    detail = parse_robota_ua_detail(DETAIL_PAGE)

    assert detail is not None
    assert detail.title == "Junior Python Developer"
    assert detail.company == "Example Labs"
    assert detail.location_text == "Київ"
    assert detail.salary_text == "40 000 — 60 000 ₴"
    assert detail.employment_type == "full_time"
    assert detail.published_at == datetime(2026, 9, 5, tzinfo=UTC)
    assert detail.is_remote is True
    assert "FastAPI and PostgreSQL" in detail.description
    assert "Recruiter Name" not in detail.description
    assert "Створіть резюме" not in detail.description


def test_robota_ua_api_parser_extracts_structured_fields() -> None:
    detail = parse_robota_ua_api_detail(
        api_detail_response("111"),
        expected_external_id="111",
        is_remote=True,
    )

    assert detail is not None
    assert detail.title == "Junior Python Developer"
    assert detail.company == "Example Labs"
    assert detail.description == (
        "We build production Python services. Requirements: FastAPI and PostgreSQL."
    )
    assert detail.location_text == "Київ"
    assert detail.salary_text == "40000 — 60000 ₴"
    assert detail.employment_type == "full_time"
    assert detail.published_at == datetime(2026, 9, 5, 10, 15, 30, 123000, tzinfo=UTC)
    assert detail.is_remote is True


def test_robota_ua_salary_parser_handles_grouped_uah_range() -> None:
    assert parse_robota_ua_salary("40 000 — 60 000 ₴") == (
        Decimal("40000"),
        Decimal("60000"),
        "UAH",
    )


@pytest.mark.asyncio
async def test_robota_ua_source_filters_onsite_jobs_and_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.path == "/vacancy":
            return httpx.Response(200, text=api_detail_response(request.url.params["id"]))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["111", "222"]
    assert str(listings[0].source_url) == "https://robota.ua/company123/vacancy111"
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.company == "Example Labs"
    assert normalized.description is not None
    assert normalized.location_text == "Київ"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("40000")
    assert normalized.salary_max == Decimal("60000")
    assert normalized.salary_currency == "UAH"
    assert normalized.salary_period == "month"
    assert normalized.published_at == datetime(2026, 9, 5, 10, 15, 30, 123000, tzinfo=UTC)
    assert listings[0].payload["published_at"] == "2026-09-05T10:15:30.123000+00:00"
    assert build_content_hash(normalized, listings[0].payload)

    metrics = source.consume_run_metrics()
    assert metrics.page_count == 1
    assert metrics.candidate_count == 3
    assert metrics.filtered_count == 1
    assert metrics.detail_failure_count == 0


@pytest.mark.asyncio
async def test_robota_ua_source_uses_platform_pagination_and_detail_cache() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/zapros/python-remote/ukraine/params;page=2":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.path == "/vacancy":
            return httpx.Response(200, text=api_detail_response(request.url.params["id"]))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=2,
            client=client,
        )
        first = [listing async for listing in source.fetch()]
        source.prime_listing_cache(
            {
                listing.external_id: CachedListing(
                    payload=listing.payload,
                    detail_fetched_at=listing.detail_fetched_at,
                )
                for listing in first
            }
        )
        second = [listing async for listing in source.fetch()]

    assert len(first) == len(second) == 2
    assert sum("/zapros/python-remote/ukraine/params;page=2" in url for url in requested_urls) == 2
    assert sum("/vacancy?id=111" in url for url in requested_urls) == 1
    assert sum("/vacancy?id=222" in url for url in requested_urls) == 1
    assert second[0].detail_fetched_at == first[0].detail_fetched_at


@pytest.mark.asyncio
async def test_robota_ua_source_continues_after_one_detail_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.params.get("id") == "111":
            raise httpx.ReadTimeout("detail timed out", request=request)
        if request.url.host == "reader.test" and request.url.path == "/company123/vacancy111":
            raise httpx.ReadTimeout("fallback timed out", request=request)
        if request.url.host == "api-reader.test" and request.url.params.get("id") == "222":
            return httpx.Response(200, text=api_detail_response("222"))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=1,
            retry_attempts=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["222"]
    metrics = source.consume_run_metrics()
    assert metrics.detail_failure_count == 1
    assert source.consume_warnings() == (
        "Robota.ua reader request failed (ReadTimeout). Falling back to the vacancy page.",
        "Robota.ua reader request failed (ReadTimeout).",
    )


@pytest.mark.asyncio
async def test_robota_ua_source_falls_back_to_vacancy_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.path == "/vacancy":
            return httpx.Response(200, text="not JSON")
        if request.url.path in {"/company123/vacancy111", "/company456/vacancy222"}:
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["111", "222"]
    assert source.consume_warnings() == (
        "Robota.ua API reader returned invalid JSON. Falling back to the vacancy page.",
        "Robota.ua API reader returned invalid JSON. Falling back to the vacancy page.",
    )


@pytest.mark.asyncio
async def test_robota_ua_source_skips_inactive_api_vacancy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.path == "/vacancy":
            return httpx.Response(
                200,
                text=api_detail_response(request.url.params["id"], active=False),
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert listings == []
    assert source.consume_run_metrics().detail_failure_count == 2


@pytest.mark.asyncio
async def test_robota_ua_source_retries_rate_limited_api_detail() -> None:
    api_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_attempts
        if request.url.path == "/zapros/python-remote/ukraine":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.host == "api-reader.test" and request.url.path == "/vacancy":
            api_attempts += 1
            if api_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, text=api_detail_response(request.url.params["id"]))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RobotaUaSource(
            search_urls=("https://robota.ua/zapros/python-remote/ukraine",),
            reader_base_url="https://reader.test",
            api_reader_base_url="https://api-reader.test",
            max_pages_per_search=1,
            retry_attempts=2,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["111", "222"]
    assert api_attempts == 3


def test_registry_builds_robota_ua_with_safe_poll_interval() -> None:
    settings = Settings(
        _env_file=None,
        djinni_source_enabled=False,
        freelancer_source_enabled=False,
        workua_source_enabled=False,
        robota_ua_source_enabled=True,
        jobs_cz_source_enabled=False,
        startupjobs_cz_source_enabled=False,
        prace_cz_source_enabled=False,
        freelance_cz_source_enabled=False,
        startup_jobs_source_enabled=False,
        jobicy_source_enabled=False,
        we_work_remotely_source_enabled=False,
        dou_jobs_source_enabled=False,
        himalayas_source_enabled=False,
        the_muse_source_enabled=False,
        ats_source_enabled=False,
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert sources[0].name == "robota_ua"
    assert sources[0].display_name == "Robota.ua"
    assert settings.source_poll_interval_seconds("robota_ua") == 21600
