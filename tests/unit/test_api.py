import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.api.app import create_app
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import MockSource


@pytest.mark.asyncio
async def test_health_and_read_only_endpoints(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    application = create_app(sqlite_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        health_response = await client.get("/health")
        ready_response = await client.get("/ready")
        jobs_response = await client.get("/jobs")
        query_response = await client.get("/jobs", params={"q": "Django"})
        salary_response = await client.get("/jobs", params={"min_salary": "1500"})
        onsite_response = await client.get("/jobs", params={"work_mode": "onsite"})
        matches_response = await client.get("/matches")
        sources_response = await client.get("/sources")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert ready_response.status_code == 200
    assert jobs_response.status_code == 200
    assert jobs_response.json()["total"] == 2
    assert len(jobs_response.json()["items"]) == 2
    assert query_response.json()["total"] == 1
    assert salary_response.json()["total"] == 1
    assert onsite_response.json()["total"] == 0
    assert matches_response.status_code == 200
    assert matches_response.json()["total"] == 2
    assert matches_response.json()["items"][0]["score"] >= 55
    assert matches_response.json()["items"][0]["reasons"]
    assert sources_response.status_code == 200
    assert sources_response.json()[0]["name"] == "mock"
