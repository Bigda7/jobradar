import json
from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.sources.base import CachedListing
from jobradar.sources.freelance_cz import FreelanceCzSource

PROJECT_SUMMARY = {
    "id": 32628,
    "projectTitle": "Build a React restaurant application",
    "remote": "remote",
    "linkToDetail": "/project/32628",
}

PROJECT_DETAIL = {
    "id": 32628,
    "title": "Build a React restaurant application",
    "description": "Create a React interface and connect a REST API.",
    "descriptionTruncated": True,
    "type": "project_only",
    "visibility": "online",
    "remote": "remote",
    "hourRate": 900,
    "createdAt": "2026-08-20T10:30:00Z",
    "skillTags": [
        {"id": "1", "skillName": "React"},
        {"id": "2", "skillName": "JavaScript"},
    ],
}


@pytest.mark.asyncio
async def test_freelance_cz_uses_public_remote_filter_and_normalizes_project() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/ui/projects/search":
            body = json.loads(request.content)
            assert body["category"] == "programovani-it"
            assert body["projectsFilter"]["remotePreference"] == [
                {"id": "remote", "checked": True, "name": "remote"}
            ]
            return httpx.Response(
                200,
                json={
                    "projects": [PROJECT_SUMMARY],
                    "pagination": {"currentPage": 1, "pagesCount": 1},
                },
            )
        if request.url.path == "/api/ui/projects/32628":
            return httpx.Response(200, json=PROJECT_DETAIL)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = FreelanceCzSource(client=client)
        listings = [listing async for listing in source.fetch()]

    assert requested_paths == ["/api/ui/projects/search", "/api/ui/projects/32628"]
    assert len(listings) == 1
    assert listings[0].payload["description_truncated"] is True
    assert listings[0].payload["jobs"] == [
        {"name": "React"},
        {"name": "JavaScript"},
    ]

    normalized = source.normalize(listings[0])
    assert normalized.kind is OpportunityKind.FREELANCE_PROJECT
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.contract_type == "hourly"
    assert normalized.salary_min == Decimal("900")
    assert normalized.salary_max == Decimal("900")
    assert normalized.salary_currency == "CZK"
    assert normalized.salary_period == "hour"


@pytest.mark.asyncio
async def test_freelance_cz_rejects_permanent_employment() -> None:
    permanent_detail = {**PROJECT_DETAIL, "type": "permanent_employment"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "projects": [PROJECT_SUMMARY],
                    "pagination": {"currentPage": 1, "pagesCount": 1},
                },
            )
        return httpx.Response(200, json=permanent_detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = FreelanceCzSource(client=client)
        listings = [listing async for listing in source.fetch()]

    assert listings == []


@pytest.mark.asyncio
async def test_freelance_cz_reuses_cached_detail_for_unchanged_summary() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "projects": [PROJECT_SUMMARY],
                    "pagination": {"currentPage": 1, "pagesCount": 1},
                },
            )
        if request.url.path.endswith("/32628"):
            return httpx.Response(200, json=PROJECT_DETAIL)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = FreelanceCzSource(client=client)
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
    assert requested_paths.count("/api/ui/projects/search") == 2
    assert requested_paths.count("/api/ui/projects/32628") == 1
