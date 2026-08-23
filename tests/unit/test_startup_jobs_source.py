from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from jobradar.config import Settings
from jobradar.domain.enums import WorkMode
from jobradar.sources.registry import build_source_registry
from jobradar.sources.startup_jobs import StartupJobsSource


def _job(
    job_id: int,
    *,
    workplace_type: str = "remote",
    salary: str | None = "USD 1,000 - 1,500 per month",
) -> dict[str, object]:
    return {
        "id": job_id,
        "title": "Junior Full-Stack Engineer",
        "url": f"https://startup.jobs/jobs/{job_id}",
        "published_at": "2026-08-21T12:00:00Z",
        "employment_type": "full-time",
        "workplace_type": workplace_type,
        "location": {
            "city": None,
            "state": None,
            "country": "Europe",
            "country_code": None,
        },
        "roles": [{"title": "Full-Stack Engineer", "slug": "full-stack-engineer"}],
        "salary": salary,
        "salary_data": None,
        "company": {"name": "Example Startup", "slug": "example-startup"},
        "description_html": (
            "<p>Build Django REST APIs and a React TypeScript interface.</p>"
            "<p>Source: <a href='https://startup.jobs'>Startup Jobs</a></p>"
        ),
    }


@pytest.mark.asyncio
async def test_source_filters_remote_paginates_and_normalizes_full_description() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("starting_after") == "101":
            second = _job(102, salary=None)
            second["salary_data"] = {
                "min": 80000,
                "max": 120000,
                "currency": "EUR",
                "interval": "year",
            }
            return httpx.Response(
                200,
                json={"data": [second], "has_more": False, "next_cursor": None},
            )
        return httpx.Response(
            200,
            json={
                "data": [_job(101), _job(999, workplace_type="hybrid")],
                "has_more": True,
                "next_cursor": 101,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = StartupJobsSource(
            api_key="sj_test",
            api_base_url="https://api.startup.test",
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Bearer sj_test"
    assert requests[0].url.params["workplace_type"] == "remote"
    assert requests[0].url.params["role"] == "engineering"
    assert [item.external_id for item in listings] == ["101", "102"]

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Full-Stack Engineer"
    assert normalized.company == "Example Startup"
    assert normalized.description is not None
    assert "Django REST APIs" in normalized.description
    assert "Source: Startup Jobs" in normalized.description
    assert normalized.location_text == "Europe"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("1000")
    assert normalized.salary_max == Decimal("1500")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "month"

    structured = source.normalize(listings[1])
    assert structured.salary_min == Decimal("80000")
    assert structured.salary_max == Decimal("120000")
    assert structured.salary_currency == "EUR"
    assert structured.salary_period == "year"


def test_settings_require_api_key_when_startup_jobs_is_enabled() -> None:
    with pytest.raises(ValidationError, match="STARTUP_JOBS_API_KEY"):
        Settings(
            _env_file=None,
            startup_jobs_source_enabled=True,
            startup_jobs_api_key=None,
        )


def test_registry_builds_startup_jobs_source() -> None:
    settings = Settings(
        _env_file=None,
        djinni_source_enabled=False,
        freelancer_source_enabled=False,
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
        startup_jobs_source_enabled=True,
        startup_jobs_api_key=SecretStr("sj_test"),
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert isinstance(sources[0], StartupJobsSource)
    assert settings.source_poll_interval_seconds("startup_jobs") == 21600
