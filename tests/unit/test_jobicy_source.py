from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.jobicy import JobicySource


@pytest.mark.asyncio
async def test_jobicy_source_requests_engineering_jobs_and_normalizes_full_description() -> None:
    requests: list[httpx.Request] = []
    payload = {
        "jobCount": 1,
        "jobs": [
            {
                "id": 151391,
                "url": "https://jobicy.com/jobs/151391-junior-full-stack-developer",
                "jobTitle": "Junior Full-Stack Developer",
                "companyName": "Example Startup",
                "jobIndustry": ["Software Engineering"],
                "jobType": ["Full-Time"],
                "jobGeo": "Europe",
                "jobLevel": "Entry-Level",
                "jobDescription": (
                    "<p>Build Django REST APIs and React TypeScript interfaces.</p>"
                ),
                "pubDate": "2026-08-22T14:18:30+02:00",
                "salaryMin": 1200,
                "salaryMax": 1800,
                "salaryCurrency": "USD",
                "salaryPeriod": "monthly",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = JobicySource(
            api_url="https://jobicy.test/api/v2/remote-jobs",
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(requests) == 1
    assert requests[0].url.params["count"] == "100"
    assert requests[0].url.params["industry"] == "engineering"
    assert len(listings) == 1
    assert listings[0].external_id == "151391"

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Full-Stack Developer"
    assert normalized.company == "Example Startup"
    assert normalized.description == "Build Django REST APIs and React TypeScript interfaces."
    assert normalized.location_text == "Europe"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("1200")
    assert normalized.salary_max == Decimal("1800")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "month"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T12:18:30+00:00"
