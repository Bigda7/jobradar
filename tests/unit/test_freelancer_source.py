import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.config import Settings
from jobradar.db.models import Listing, Opportunity
from jobradar.domain.enums import OpportunityKind, RunStatus, WorkMode
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.sources.freelancer import (
    FreelancerApiClient,
    FreelancerAuthenticationError,
    FreelancerSource,
)
from jobradar.sources.registry import build_source_registry

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "freelancer" / "search_projects.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _source(client: httpx.AsyncClient) -> FreelancerSource:
    return FreelancerSource(
        api_client=FreelancerApiClient(
            oauth_token="test-oauth-token",
            api_base_url="https://freelancer.test/api/projects/0.1",
            client=client,
        ),
        search_queries=("python django", "react typescript"),
        web_base_url="https://freelancer.test",
        page_size=50,
        max_pages_per_query=2,
    )


@pytest.mark.asyncio
async def test_api_client_uses_official_search_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/projects/0.1/projects/active/"
        assert request.headers["Freelancer-OAuth-V1"] == "test-oauth-token"
        assert request.url.params["query"] == "python django"
        assert request.url.params["limit"] == "25"
        assert request.url.params["offset"] == "50"
        assert request.url.params["sort_field"] == "time_updated"
        assert request.url.params["full_description"] == "true"
        assert request.url.params["job_details"] == "true"
        return httpx.Response(200, json=_fixture())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FreelancerApiClient(
            oauth_token="test-oauth-token",
            api_base_url="https://freelancer.test/api/projects/0.1",
            client=http_client,
        )
        async with client:
            page = await client.search_active_projects(
                "python django",
                limit=25,
                offset=50,
            )

    assert page.total_count == 2
    assert len(page.projects) == 2
    assert page.users["7001"]["username"] == "verified-employer"


@pytest.mark.asyncio
async def test_api_client_reports_authentication_failure_without_leaking_token() -> None:
    response_payload = {
        "status": "error",
        "message": "Invalid access token",
        "error_code": "AUTHENTICATION_ERROR",
        "request_id": "request-401",
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json=response_payload))
    ) as http_client:
        client = FreelancerApiClient(oauth_token="secret-value", client=http_client)
        with pytest.raises(FreelancerAuthenticationError) as captured:
            async with client:
                await client.search_active_projects("python", limit=10, offset=0)

    message = str(captured.value)
    assert "Invalid access token" in message
    assert "request-401" in message
    assert "secret-value" not in message


@pytest.mark.asyncio
async def test_source_filters_local_projects_deduplicates_and_normalizes() -> None:
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_fixture())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = _source(client)
        listings = [listing async for listing in source.fetch()]

    assert request_count == 2
    assert len(listings) == 1
    assert listings[0].external_id == "501001"
    assert str(listings[0].source_url) == (
        "https://freelancer.test/projects/python-react/build-django-react-dashboard"
    )
    assert listings[0].payload["_owner"]["status"]["payment_verified"] is True

    normalized = source.normalize(listings[0])
    assert normalized.kind is OpportunityKind.FREELANCE_PROJECT
    assert normalized.title == "Build a Django and React dashboard"
    assert normalized.company == "Verified Employer"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.location_text == "Remote"
    assert normalized.contract_type == "fixed"
    assert normalized.salary_min == Decimal("300")
    assert normalized.salary_max == Decimal("600")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "project"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T08:00:00+00:00"


@pytest.mark.asyncio
async def test_source_marks_explicitly_closed_projects_unavailable() -> None:
    payload = _fixture()
    result = payload["result"]
    assert isinstance(result, dict)
    projects = result["projects"]
    assert isinstance(projects, list)
    projects[0]["status"] = "closed"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        listings = [listing async for listing in _source(client).fetch()]

    assert len(listings) == 1
    assert listings[0].is_available is False


@pytest.mark.asyncio
async def test_freelancer_ingestion_is_idempotent(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_fixture()))
    ) as client:
        source = _source(client)
        service = IngestionService(sqlite_session_factory)
        first = await service.run_source(source)
        second = await service.run_source(source)

    assert first.status is RunStatus.SUCCEEDED
    assert first.created == 1
    assert second.status is RunStatus.SUCCEEDED
    assert second.created == 0
    assert second.unchanged == 1

    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 1
        opportunity = await session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.kind == OpportunityKind.FREELANCE_PROJECT.value

    matching_summary = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    assert matching_summary.evaluated == 1
    assert matching_summary.unchanged == 0


def test_settings_require_token_when_freelancer_is_enabled() -> None:
    with pytest.raises(ValidationError, match="FREELANCER_OAUTH_TOKEN"):
        Settings(
            _env_file=None,
            freelancer_source_enabled=True,
            freelancer_oauth_token=None,
        )


def test_registry_builds_freelancer_source_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        djinni_source_enabled=False,
        freelancer_source_enabled=True,
        workua_source_enabled=False,
        jobs_cz_source_enabled=False,
        startupjobs_cz_source_enabled=False,
        prace_cz_source_enabled=False,
        freelance_cz_source_enabled=False,
        jobicy_source_enabled=False,
        we_work_remotely_source_enabled=False,
        dou_jobs_source_enabled=False,
        himalayas_source_enabled=False,
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
        freelancer_oauth_token=SecretStr("test-oauth-token"),
        freelancer_search_queries="python django; react typescript ",
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert isinstance(sources[0], FreelancerSource)
