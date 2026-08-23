import json
from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.base import CachedListing
from jobradar.sources.startupjobs_cz import StartupJobsCzSource

REMOTE_SUMMARY = {
    "id": 106825,
    "name": {"cs": "Junior Python Developer"},
    "slug": {"cs": "junior-python-developer"},
    "locationPreferences": ["remote"],
    "company": {"name": "Remote Startup"},
}

MIXED_SUMMARY = {
    "id": 105839,
    "name": {"cs": "Python Developer"},
    "slug": {"cs": "python-developer"},
    "locationPreferences": ["hybrid", "remote"],
    "company": {"name": "Hybrid Startup"},
}

REMOTE_DETAIL = {
    "id": 106825,
    "state": "published",
    "publishedAt": "2026-08-13T09:11:13+02:00",
    "name": {"cs": "Junior Python Developer"},
    "slug": {"cs": "junior-python-developer"},
    "description": {"cs": "<p>Python and Django development.</p><p>Build REST APIs.</p>"},
    "locationPreferences": [{"@id": "/api/.well-known/genid/generated-value", "id": "remote"}],
    "hours": [{"id": "full-time"}],
    "contracts": [{"id": "contract"}],
    "salary": {
        "min": 6000000,
        "max": 9000000,
        "currency": "CZK",
        "period": "month",
    },
}


@pytest.mark.asyncio
async def test_startupjobs_cz_keeps_only_exclusively_remote_offers() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/search-offers":
            body = json.loads(request.content)
            assert body == {
                "query": "python django",
                "locationPreferences": ["remote"],
            }
            return httpx.Response(
                200,
                json={
                    "member": [REMOTE_SUMMARY, MIXED_SUMMARY],
                    "view": {"first": "/api/search-offers?page=1"},
                },
            )
        if request.url.path == "/api/offers/106825":
            return httpx.Response(200, json=REMOTE_DETAIL)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = StartupJobsCzSource(
            api_base_url="https://back.startupjobs.test",
            web_base_url="https://www.startupjobs.test",
            search_queries=("python django",),
            max_pages_per_query=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert requested_paths == ["/api/search-offers", "/api/offers/106825"]
    assert len(listings) == 1
    assert listings[0].external_id == "106825"
    assert "@id" not in listings[0].payload["locationPreferences"][0]
    assert str(listings[0].source_url) == (
        "https://www.startupjobs.test/nabidka/106825/junior-python-developer"
    )

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.company == "Remote Startup"
    assert normalized.description == "Python and Django development. Build REST APIs."
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.contract_type == "contract"
    assert normalized.salary_min == Decimal("60000")
    assert normalized.salary_max == Decimal("90000")
    assert normalized.salary_currency == "CZK"
    assert normalized.salary_period == "month"


@pytest.mark.asyncio
async def test_startupjobs_cz_rechecks_remote_mode_on_detail() -> None:
    mixed_detail = {
        **REMOTE_DETAIL,
        "locationPreferences": [{"id": "hybrid"}, {"id": "remote"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search-offers":
            return httpx.Response(
                200,
                json={"member": [REMOTE_SUMMARY], "view": {}},
            )
        return httpx.Response(200, json=mixed_detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = StartupJobsCzSource(
            search_queries=("python",),
            max_pages_per_query=1,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert listings == []


@pytest.mark.asyncio
async def test_startupjobs_cz_reuses_cached_detail_for_unchanged_summary() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/search-offers":
            return httpx.Response(200, json={"member": [REMOTE_SUMMARY], "view": {}})
        if request.url.path == "/api/offers/106825":
            return httpx.Response(200, json=REMOTE_DETAIL)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = StartupJobsCzSource(
            search_queries=("python",),
            max_pages_per_query=1,
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

    assert len(second) == 1
    assert requested_paths.count("/api/search-offers") == 2
    assert requested_paths.count("/api/offers/106825") == 1
