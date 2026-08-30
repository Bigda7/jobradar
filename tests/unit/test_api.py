import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.api.app import create_app
from jobradar.config import Settings
from jobradar.db.models import Listing, Opportunity, Source
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
    assert jobs_response.json()["items"][0]["source_url"].startswith("https://")
    assert jobs_response.json()["items"][0]["source_name"] == "mock"
    assert jobs_response.json()["items"][0]["source_display_name"] == "Mock Source"
    assert jobs_response.json()["items"][0]["first_seen_at"].endswith("Z")
    assert query_response.json()["total"] == 1
    assert salary_response.json()["total"] == 1
    assert onsite_response.json()["total"] == 0
    assert matches_response.status_code == 200
    assert matches_response.json()["total"] == 2
    assert matches_response.json()["items"][0]["score"] >= 55
    assert matches_response.json()["items"][0]["reasons"]
    assert matches_response.json()["items"][0]["matched_skills"]
    assert sources_response.status_code == 200
    assert sources_response.json()[0]["name"] == "mock"
    assert health_response.headers["x-content-type-options"] == "nosniff"
    assert health_response.headers["x-frame-options"] == "DENY"
    assert health_response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert health_response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_matches_filter_uses_the_selected_source_listing(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)

    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(select(Opportunity).order_by(Opportunity.id))
        assert opportunity is not None

        alternate_source = Source(
            name="alternate",
            display_name="Alternate Jobs",
            enabled=True,
        )
        session.add(alternate_source)
        await session.flush()
        session.add(
            Listing(
                source_id=alternate_source.id,
                opportunity_id=opportunity.id,
                external_id="alternate-1",
                source_url="https://alternate.example/jobs/1",
                canonical_url="https://alternate.example/jobs/1",
                content_hash="alternate-content-hash",
                raw_data={},
                normalized_data={},
                quality_score=100,
                is_active=True,
            )
        )
        await session.commit()

    application = create_app(sqlite_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        filtered_response = await client.get(
            "/matches",
            params={"source": " ALTERNATE "},
        )
        missing_response = await client.get(
            "/matches",
            params={"source": "missing"},
        )

    assert filtered_response.status_code == 200
    assert filtered_response.json()["total"] == 1
    assert len(filtered_response.json()["items"]) == 1
    assert filtered_response.json()["items"][0]["source_name"] == "alternate"
    assert filtered_response.json()["items"][0]["source_display_name"] == "Alternate Jobs"
    assert filtered_response.json()["items"][0]["source_url"] == "https://alternate.example/jobs/1"
    assert missing_response.status_code == 200
    assert missing_response.json()["total"] == 0
    assert missing_response.json()["items"] == []


@pytest.mark.asyncio
async def test_cors_allows_configured_frontend_and_rejects_other_origins(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(
        sqlite_session_factory,
        application_settings=Settings(
            cors_allowed_origins=("http://localhost:5173;https://jobradar-frontend.vercel.app/")
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        allowed_response = await client.get(
            "/health",
            headers={"Origin": "https://jobradar-frontend.vercel.app"},
        )
        denied_response = await client.get(
            "/health",
            headers={"Origin": "https://untrusted.example"},
        )
        preflight_response = await client.options(
            "/matches",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert allowed_response.status_code == 200
    assert (
        allowed_response.headers["access-control-allow-origin"]
        == "https://jobradar-frontend.vercel.app"
    )
    assert "access-control-allow-origin" not in denied_response.headers
    assert preflight_response.status_code == 200
    assert preflight_response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert preflight_response.headers["access-control-allow-methods"] == "GET"


@pytest.mark.asyncio
async def test_api_rejects_untrusted_host(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(sqlite_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/health", headers={"Host": "untrusted.example"})

    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_production_api_enables_hsts(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(
        sqlite_session_factory,
        application_settings=Settings(
            app_env="production",
            api_allowed_hosts="test;api.example.com",
            api_bearer_token="a" * 32,
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/health")

    assert response.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")


@pytest.mark.asyncio
async def test_data_endpoints_require_configured_bearer_token(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(
        sqlite_session_factory,
        application_settings=Settings(api_bearer_token="a" * 32),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        health_response = await client.get("/health")
        unauthorized_response = await client.get("/jobs")
        invalid_response = await client.get(
            "/jobs",
            headers={"Authorization": "Bearer invalid"},
        )
        authorized_response = await client.get(
            "/jobs",
            headers={"Authorization": f"Bearer {'a' * 32}"},
        )

    assert health_response.status_code == 200
    assert unauthorized_response.status_code == 401
    assert unauthorized_response.headers["www-authenticate"] == "Bearer"
    assert invalid_response.status_code == 401
    assert authorized_response.status_code == 200


@pytest.mark.asyncio
async def test_production_disables_api_documentation(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(
        sqlite_session_factory,
        application_settings=Settings(
            app_env="production",
            api_allowed_hosts="test;api.example.com",
            api_bearer_token="a" * 32,
            database_url="sqlite+aiosqlite:///:memory:",
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        docs_response = await client.get("/docs")
        schema_response = await client.get("/openapi.json")

    assert docs_response.status_code == 404
    assert schema_response.status_code == 404


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_is_unavailable(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_execute(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(AsyncSession, "execute", failed_execute)
    application = create_app(sqlite_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable."}


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_check_times_out(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_execute(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(0.1)

    monkeypatch.setattr(AsyncSession, "execute", slow_execute)
    application = create_app(
        sqlite_session_factory,
        application_settings=Settings(readiness_timeout_seconds=0.01),
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable."}


@pytest.mark.asyncio
async def test_query_parameters_have_bounded_pagination_and_values(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = create_app(sqlite_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        responses = [
            await client.get("/jobs", params={"limit": 201}),
            await client.get("/jobs", params={"offset": -1}),
            await client.get("/jobs", params={"offset": 100_001}),
            await client.get("/jobs", params={"q": "  "}),
            await client.get("/jobs", params={"min_salary": "1000000001"}),
            await client.get("/matches", params={"min_score": 101}),
            await client.get("/matches", params={"offset": 100_001}),
            await client.get("/matches", params={"source": " "}),
        ]

    assert all(response.status_code == 422 for response in responses)
