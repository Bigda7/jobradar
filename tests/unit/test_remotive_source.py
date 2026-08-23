from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.remotive import RemotiveSource, parse_salary


@pytest.mark.asyncio
async def test_remotive_source_requests_software_jobs_and_normalizes_salary() -> None:
    requests: list[httpx.Request] = []
    payload = {
        "job-count": 1,
        "jobs": [
            {
                "id": 2091105,
                "url": "https://remotive.test/remote-jobs/software-dev/junior-developer",
                "title": "Junior Full-Stack Developer",
                "company_name": "Example Labs",
                "category": "Software Development",
                "tags": ["Python", "React"],
                "job_type": "full_time",
                "publication_date": "2026-08-22T08:30:00",
                "candidate_required_location": "Europe",
                "salary": "$40k - $55,000 per year",
                "description": "<p>Build Django REST APIs and React applications.</p>",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = RemotiveSource(
            api_url="https://remotive.test/api/remote-jobs",
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(requests) == 1
    assert requests[0].url.params["category"] == "software-dev"
    assert requests[0].url.params["limit"] == "100"
    assert len(listings) == 1
    assert listings[0].external_id == "2091105"

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Full-Stack Developer"
    assert normalized.company == "Example Labs"
    assert normalized.description == "Build Django REST APIs and React applications."
    assert normalized.location_text == "Europe"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("40000")
    assert normalized.salary_max == Decimal("55000")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "year"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T08:30:00+00:00"


@pytest.mark.parametrize(
    ("raw", "minimum", "maximum", "currency", "period"),
    [
        ("$14/hour", "14", "14", "USD", "hour"),
        ("EUR 2,000 - 3k monthly", "2000", "3000", "EUR", "month"),
        ("80,000 - 100,000 CZK", "80000", "100000", "CZK", "year"),
    ],
)
def test_parse_salary(
    raw: str,
    minimum: str,
    maximum: str,
    currency: str,
    period: str,
) -> None:
    salary = parse_salary(raw)

    assert salary is not None
    assert salary.minimum == Decimal(minimum)
    assert salary.maximum == Decimal(maximum)
    assert salary.currency == currency
    assert salary.period == period


def test_parse_salary_rejects_amount_without_currency() -> None:
    assert parse_salary("Competitive salary") is None
    assert parse_salary("5000 monthly") is None
