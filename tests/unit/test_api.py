import asyncio
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.api.app import create_app
from jobradar.config import Settings
from jobradar.db.models import Listing, MatchEvaluation, Opportunity, Source, SourceRun
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


@pytest.mark.asyncio
async def test_api_returns_update_dates_and_uses_them_for_newest_order(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    async with sqlite_session_factory() as session, session.begin():
        jobs = list((await session.scalars(select(Opportunity).order_by(Opportunity.id))).all())
        updated_id = jobs[0].id
        jobs[0].published_at = None
        jobs[0].source_updated_at = datetime(2026, 10, 5, 12, tzinfo=UTC)
    application = create_app(sqlite_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/jobs")
    assert response.status_code == 200
    newest = response.json()["items"][0]
    assert newest["id"] == updated_id
    assert newest["published_at"] is None
    assert newest["source_updated_at"] == "2026-10-05T12:00:00Z"


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
    assert sources_response.json()[0]["last_run_status"] == "succeeded"
    assert sources_response.json()[0]["last_discovered_count"] == 2
    assert sources_response.json()[0]["last_candidate_count"] == 2
    assert sources_response.json()[0]["last_filtered_count"] == 0
    assert sources_response.json()[0]["last_detail_failure_count"] == 0
    assert sources_response.json()[0]["last_page_count"] == 0
    assert sources_response.json()[0]["last_limit_reached"] is False
    assert sources_response.json()[0]["last_created_count"] == 2
    assert sources_response.json()[0]["last_updated_count"] == 0
    assert sources_response.json()[0]["last_duplicate_count"] == 0
    assert sources_response.json()[0]["last_normalization_error_count"] == 0
    assert sources_response.json()[0]["last_warning_count"] == 0
    assert sources_response.json()[0]["last_error_count"] == 0
    assert sources_response.json()[0]["last_coverage_warning"] is None
    assert health_response.headers["x-content-type-options"] == "nosniff"
    assert health_response.headers["x-frame-options"] == "DENY"
    assert health_response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert health_response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_public_sources_hide_diagnostics_but_preserve_issue_metrics(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    diagnostic = "Reader failed at internal.example/private-path with unrecognized credential text."
    async with sqlite_session_factory() as session, session.begin():
        source = Source(
            name="test_source",
            display_name="Test Source",
            enabled=True,
            last_error=diagnostic,
        )
        session.add(source)
        await session.flush()
        session.add(
            SourceRun(
                source_id=source.id,
                status="partial",
                started_at=datetime(2026, 9, 28, tzinfo=UTC),
                discovered_count=1,
                error_count=1,
                error_message=diagnostic,
            )
        )

    application = create_app(sqlite_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/sources")

    assert response.status_code == 200
    assert len(response.json()) == 1
    public_source = response.json()[0]
    assert public_source["last_error"] == (
        "Source reported an issue. Details are available internally."
    )
    assert diagnostic not in response.text
    assert public_source["last_run_status"] == "partial"
    assert public_source["last_discovered_count"] == 1
    assert public_source["last_error_count"] == 1
    async with sqlite_session_factory() as session:
        stored_source = await session.scalar(select(Source).where(Source.name == "test_source"))
        stored_run = await session.scalar(select(SourceRun))
        assert stored_source is not None
        assert stored_source.last_error == diagnostic
        assert stored_run is not None
        assert stored_run.error_message == diagnostic


@pytest.mark.asyncio
async def test_minimum_salary_only_compares_monthly_amounts_in_selected_currency(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    usd_monthly = deepcopy(DEFAULT_LISTINGS[0])
    uah_monthly = deepcopy(usd_monthly)
    uah_monthly.update(
        {
            "id": "uah-monthly",
            "url": "https://example.com/jobs/uah-monthly",
            "title": "UAH Monthly Developer",
            "company": "Another Company",
            "salary_min": "50000",
            "salary_max": "70000",
            "salary_currency": "UAH",
        }
    )
    usd_hourly = deepcopy(usd_monthly)
    usd_hourly.update(
        {
            "id": "usd-hourly",
            "url": "https://example.com/jobs/usd-hourly",
            "title": "USD Hourly Developer",
            "company": "Hourly Company",
            "salary_min": "2000",
            "salary_max": "3000",
            "salary_period": "hour",
        }
    )
    usd_from = deepcopy(usd_monthly)
    usd_from.update(
        {
            "id": "usd-from",
            "url": "https://example.com/jobs/usd-from",
            "title": "USD From Developer",
            "company": "From Company",
            "salary_min": "1600",
            "salary_max": None,
        }
    )
    await IngestionService(sqlite_session_factory).run_source(
        MockSource((usd_monthly, uah_monthly, usd_hourly, usd_from))
    )
    application = create_app(sqlite_session_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        usd_response = await client.get("/jobs", params={"min_salary": "1500"})
        uah_response = await client.get(
            "/jobs", params={"min_salary": "50000", "salary_currency": "UAH"}
        )
        invalid_currency = await client.get(
            "/jobs", params={"min_salary": "1000", "salary_currency": "invalid"}
        )

    assert usd_response.status_code == 200
    assert {job["title"] for job in usd_response.json()["items"]} == {
        "Junior Full-Stack Developer",
        "USD From Developer",
    }
    assert uah_response.status_code == 200
    assert [job["title"] for job in uah_response.json()["items"]] == ["UAH Monthly Developer"]
    assert invalid_currency.status_code == 422


@pytest.mark.asyncio
async def test_jobs_employment_type_filter_matches_a_value_in_a_combined_field(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())

    async with sqlite_session_factory() as session:
        opportunity = await session.scalar(select(Opportunity).order_by(Opportunity.id))
        assert opportunity is not None
        opportunity.employment_type = "fulltime_permanent,part_time"
        await session.commit()

    application = create_app(sqlite_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        full_time_response = await client.get(
            "/jobs",
            params={"employment_type": "full-time"},
        )
        part_time_response = await client.get(
            "/jobs",
            params={"employment_type": "part_time"},
        )
        contractor_response = await client.get(
            "/jobs",
            params={"employment_type": "contractor"},
        )

    assert full_time_response.status_code == 200
    assert full_time_response.json()["total"] == 2
    assert part_time_response.status_code == 200
    assert part_time_response.json()["total"] == 1
    assert contractor_response.status_code == 200
    assert contractor_response.json()["total"] == 0


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
            name="djinni",
            display_name="Djinni",
            enabled=True,
        )
        session.add(alternate_source)
        await session.flush()
        session.add(
            Listing(
                source_id=alternate_source.id,
                opportunity_id=opportunity.id,
                external_id="alternate-1",
                source_url="https://djinni.co/jobs/1/",
                canonical_url="https://djinni.co/jobs/1/",
                content_hash="alternate-content-hash",
                raw_data={},
                normalized_data={},
                quality_score=100,
                is_active=True,
            )
        )
        unsafe_source = Source(
            name="dou_jobs",
            display_name="DOU Jobs",
            enabled=True,
        )
        session.add(unsafe_source)
        await session.flush()
        session.add(
            Listing(
                source_id=unsafe_source.id,
                opportunity_id=opportunity.id,
                external_id="unsafe-1",
                source_url="https://jobs.dou.ua.evil.test/vacancies/1/",
                canonical_url="https://jobs.dou.ua.evil.test/vacancies/1/",
                content_hash="unsafe-content-hash",
                raw_data={},
                normalized_data={},
                quality_score=100000,
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
            params={"source": " DJINNI "},
        )
        missing_response = await client.get(
            "/matches",
            params={"source": "missing"},
        )
        unsafe_response = await client.get(
            "/matches",
            params={"source": "dou_jobs"},
        )
        jobs_response = await client.get("/jobs")

    assert filtered_response.status_code == 200
    assert filtered_response.json()["total"] == 1
    assert len(filtered_response.json()["items"]) == 1
    assert filtered_response.json()["items"][0]["source_name"] == "djinni"
    assert filtered_response.json()["items"][0]["source_display_name"] == "Djinni"
    assert filtered_response.json()["items"][0]["source_url"] == "https://djinni.co/jobs/1/"
    assert missing_response.status_code == 200
    assert missing_response.json()["total"] == 0
    assert missing_response.json()["items"] == []
    assert unsafe_response.json()["total"] == 0
    assert unsafe_response.json()["items"] == []
    assert all("evil.test" not in item["source_url"] for item in jobs_response.json()["items"])


@pytest.mark.asyncio
async def test_matches_sort_is_applied_before_pagination(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)

    async with sqlite_session_factory() as session:
        opportunities = list(
            (await session.scalars(select(Opportunity).order_by(Opportunity.id.asc()))).all()
        )
        evaluations = {
            evaluation.opportunity_id: evaluation
            for evaluation in (await session.scalars(select(MatchEvaluation))).all()
        }
        assert len(opportunities) == 2
        older_high_score, newer_low_score = opportunities
        older_high_score.company = "Zulu Labs"
        older_high_score.published_at = datetime(2026, 8, 20, tzinfo=UTC)
        newer_low_score.company = "Alpha Labs"
        newer_low_score.published_at = datetime(2026, 8, 25, tzinfo=UTC)
        evaluations[older_high_score.id].score = 99
        evaluations[newer_low_score.id].score = 55
        await session.commit()

    application = create_app(sqlite_session_factory)
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        score_response = await client.get("/matches", params={"sort": "score", "limit": 1})
        newest_response = await client.get("/matches", params={"sort": "newest", "limit": 1})
        company_response = await client.get("/matches", params={"sort": "company", "limit": 1})
        invalid_response = await client.get("/matches", params={"sort": "unsupported"})

    assert score_response.json()["items"][0]["id"] == older_high_score.id
    assert newest_response.json()["items"][0]["id"] == newer_low_score.id
    assert company_response.json()["items"][0]["id"] == newer_low_score.id
    assert invalid_response.status_code == 422


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
