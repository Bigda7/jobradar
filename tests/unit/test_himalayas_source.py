from decimal import Decimal

import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate
from jobradar.sources.himalayas import HimalayasSource, HimalayasSourceError
from jobradar.sources.registry import build_source_registry


def _job(**changes: object) -> dict[str, object]:
    job: dict[str, object] = {
        "title": "Python Developer",
        "excerpt": "Build web applications.",
        "companyName": "Example Labs",
        "companySlug": "example-labs",
        "employmentType": "Full Time",
        "minSalary": 48000,
        "maxSalary": 60000,
        "salaryPeriod": "annual",
        "seniority": ["Entry-level"],
        "currency": "USD",
        "locationRestrictions": [],
        "timezoneRestrictions": ["UTC+0", "UTC+2"],
        "categories": ["Python", "Software Development"],
        "parentCategories": ["Engineering"],
        "description": "<p>Build Django REST APIs and React applications.</p>",
        "pubDate": 1787387400000,
        "expiryDate": 1789979400000,
        "applicationLink": "https://himalayas.test/jobs/python-developer",
        "guid": "himalayas-job-1",
    }
    job.update(changes)
    return job


def _candidate(source: HimalayasSource, job: dict[str, object]) -> MatchCandidate:
    from jobradar.domain.models import RawListing

    raw_listing = RawListing(
        external_id=str(job["guid"]),
        source_url=str(job["applicationLink"]),
        payload=job,
    )
    normalized = source.normalize(raw_listing)
    return MatchCandidate(
        kind=normalized.kind,
        title=normalized.title,
        company=normalized.company,
        description=normalized.description,
        location_text=normalized.location_text,
        work_mode=normalized.work_mode,
        employment_type=normalized.employment_type,
        contract_type=normalized.contract_type,
        salary_min=normalized.salary_min,
        salary_max=normalized.salary_max,
        salary_currency=normalized.salary_currency,
        salary_period=normalized.salary_period,
        raw_data=job,
    )


@pytest.mark.asyncio
async def test_himalayas_source_uses_cursor_pagination_and_normalizes_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json={"jobs": [_job()], "nextCursor": "opaque-page-2"},
            )
        return httpx.Response(
            200,
            json={
                "jobs": [
                    _job(),
                    _job(
                        guid="himalayas-job-2",
                        applicationLink="https://himalayas.test/jobs/react-developer",
                        title="React Developer",
                    ),
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = HimalayasSource(api_url="https://himalayas.test/jobs/api", client=client)
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == [
        "himalayas-job-1",
        "himalayas-job-2",
    ]
    assert len(requests) == 2
    assert requests[0].url.params["limit"] == "20"
    assert requests[1].url.params["cursor"] == "opaque-page-2"

    normalized = source.normalize(listings[0])
    assert normalized.kind is OpportunityKind.EMPLOYMENT
    assert normalized.title == "Python Developer"
    assert normalized.company == "Example Labs"
    assert normalized.description == "Build Django REST APIs and React applications."
    assert normalized.location_text == "Worldwide"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("48000")
    assert normalized.salary_max == Decimal("60000")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "year"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T08:30:00+00:00"
    assert listings[0].payload["seniority"] == ["Entry-level"]
    assert listings[0].payload["categories"] == ["Python", "Software Development"]


def test_himalayas_structured_seniority_and_tags_contribute_to_scoring() -> None:
    source = HimalayasSource()

    result = score_candidate(_candidate(source, _job()), BOHDAN_PROFILE)

    assert result.score >= BOHDAN_PROFILE.notification_threshold
    assert any("Структурированный уровень" in reason for reason in result.reasons)
    assert any("Python" in reason for reason in result.reasons)


def test_himalayas_high_salary_uses_shared_monthly_sanity_penalty() -> None:
    source = HimalayasSource()
    normal = score_candidate(
        _candidate(source, _job(minSalary=18000, maxSalary=24000)),
        BOHDAN_PROFILE,
    )
    high_salary = score_candidate(
        _candidate(source, _job(minSalary=24012, maxSalary=36000)),
        BOHDAN_PROFILE,
    )

    assert high_salary.score == normal.score - 20
    assert any("выше USD 2,000" in concern for concern in high_salary.concerns)


@pytest.mark.parametrize(
    ("changes", "concern"),
    (
        (
            {
                "locationRestrictions": [
                    {"alpha2": "US", "name": "United States", "slug": "united-states"}
                ]
            },
            "только кандидатам из США",
        ),
        (
            {"description": "<p>Build targeting systems for a military platform.</p>"},
            "военный рекрутинг",
        ),
    ),
)
def test_himalayas_jobs_use_shared_hard_rejections(
    changes: dict[str, object],
    concern: str,
) -> None:
    source = HimalayasSource()

    result = score_candidate(_candidate(source, _job(**changes)), BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert concern in result.concerns[0]


@pytest.mark.asyncio
async def test_himalayas_source_rejects_malformed_api_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"unexpected": []}))
    ) as client:
        source = HimalayasSource(client=client)

        with pytest.raises(HimalayasSourceError, match="jobs list"):
            _ = [listing async for listing in source.fetch()]


def test_registry_builds_himalayas_with_daily_poll_interval() -> None:
    settings = Settings(
        _env_file=None,
        djinni_source_enabled=False,
        freelancer_source_enabled=False,
        workua_source_enabled=False,
        robota_ua_source_enabled=False,
        jobs_cz_source_enabled=False,
        startupjobs_cz_source_enabled=False,
        prace_cz_source_enabled=False,
        freelance_cz_source_enabled=False,
        startup_jobs_source_enabled=False,
        jobicy_source_enabled=False,
        we_work_remotely_source_enabled=False,
        dou_jobs_source_enabled=False,
        himalayas_source_enabled=True,
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert isinstance(sources[0], HimalayasSource)
    assert sources[0].deactivate_missing_listings is False
    assert settings.source_poll_interval_seconds("himalayas") == 86400
