from decimal import Decimal

import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import RawListing
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.sanity import evaluate_sanity
from jobradar.matching.scorer import score_candidate
from jobradar.sources.registry import build_source_registry
from jobradar.sources.the_muse import TheMuseSource, TheMuseSourceError, parse_salary


def _job(**changes: object) -> dict[str, object]:
    job: dict[str, object] = {
        "contents": (
            "<p>Build React and Django REST API features with PostgreSQL.</p>"
            "<p>This is a full-time role. Base salary: $36,000-$42,000/year.</p>"
        ),
        "name": "Junior Full-Stack Developer",
        "type": "external",
        "publication_date": "2026-08-20T12:30:00Z",
        "short_name": "junior-full-stack-developer",
        "model_type": "jobs",
        "id": 21958032,
        "locations": [{"name": "Flexible / Remote"}],
        "categories": [{"name": "Software Engineering"}],
        "levels": [{"name": "Entry Level", "short_name": "entry"}],
        "tags": [{"name": "React"}, {"name": "Python"}],
        "refs": {
            "landing_page": "https://www.themuse.test/jobs/example/junior-full-stack-developer"
        },
        "company": {"id": 42, "short_name": "example", "name": "Example Labs"},
    }
    job.update(changes)
    return job


def _candidate(source: TheMuseSource, job: dict[str, object]) -> MatchCandidate:
    refs = job["refs"]
    assert isinstance(refs, dict)
    raw_listing = RawListing(
        external_id=str(job["id"]),
        source_url=str(refs["landing_page"]),
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
async def test_the_muse_uses_remote_filters_pagination_and_normalizes_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        if page == 0:
            return httpx.Response(
                200,
                json={
                    "page": 0,
                    "page_count": 2,
                    "results": [
                        _job(),
                        _job(
                            id=99,
                            locations=[{"name": "Prague, Czechia"}],
                        ),
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "page": 1,
                "page_count": 2,
                "results": [
                    _job(),
                    _job(
                        id=21958033,
                        name="React Developer",
                        refs={
                            "landing_page": "https://www.themuse.test/jobs/example/react-developer"
                        },
                        locations=[
                            {"name": "Flexible / Remote"},
                            {"name": "Prague, Czechia"},
                        ],
                    ),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TheMuseSource(
            api_url="https://www.themuse.test/api/public/jobs",
            api_key="test-key",
            categories=("Software Engineering", "Computer and IT"),
            levels=("Entry Level", "Mid Level"),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["21958032", "21958033"]
    assert len(requests) == 2
    assert requests[0].url.params.get_list("category") == [
        "Software Engineering",
        "Computer and IT",
    ]
    assert requests[0].url.params.get_list("level") == ["Entry Level", "Mid Level"]
    assert requests[0].url.params["location"] == "Flexible / Remote"
    assert requests[0].url.params["descending"] == "true"
    assert requests[0].url.params["api_key"] == "test-key"

    normalized = source.normalize(listings[0])
    assert normalized.kind is OpportunityKind.EMPLOYMENT
    assert normalized.title == "Junior Full-Stack Developer"
    assert normalized.company == "Example Labs"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.location_text == "Remote"
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("36000")
    assert normalized.salary_max == Decimal("42000")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "year"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-20T12:30:00+00:00"
    assert source.normalize(listings[1]).location_text == "Prague, Czechia"


def test_the_muse_structured_tags_and_entry_level_contribute_to_scoring() -> None:
    source = TheMuseSource()

    result = score_candidate(
        _candidate(source, _job(name="Full-Stack Developer")),
        BOHDAN_PROFILE,
    )

    assert result.score >= BOHDAN_PROFILE.notification_threshold
    assert any("Структурированный уровень" in reason for reason in result.reasons)
    assert any("React" in reason and "Python" in reason for reason in result.reasons)


def test_the_muse_salary_uses_shared_monthly_sanity_penalty() -> None:
    source = TheMuseSource()
    candidate = _candidate(
        source,
        _job(
            contents=(
                "<p>Build React and Django REST API features with PostgreSQL.</p>"
                "<p>This is a full-time role. Base pay: $60,000-$72,000/year.</p>"
            )
        ),
    )
    sanity = evaluate_sanity(candidate, BOHDAN_PROFILE)
    result = score_candidate(candidate, BOHDAN_PROFILE)

    assert sanity.score_adjustment == -20
    assert any("выше USD 2,000" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    ("contents", "concern"),
    (
        (
            "<p>Build React services. This position is only available within the US.</p>",
            "только кандидатам из США",
        ),
        (
            "<p>Build targeting software for a defense technology platform.</p>",
            "военный рекрутинг",
        ),
        ("<p>Unpaid role building a React product.</p>", "неоплачиваемая"),
    ),
)
def test_the_muse_jobs_use_shared_hard_rejections(contents: str, concern: str) -> None:
    source = TheMuseSource()

    result = score_candidate(_candidate(source, _job(contents=contents)), BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert concern in result.concerns[0]


def test_equal_opportunity_military_status_text_does_not_reject_regular_job() -> None:
    source = TheMuseSource()
    job = _job(
        contents=(
            "<p>Build React and Django APIs in a full-time role.</p>"
            "<p>We do not discriminate based on military or veteran status. "
            "Protected veterans are welcome.</p>"
        )
    )

    result = score_candidate(_candidate(source, job), BOHDAN_PROFILE)

    assert result.score > 0
    assert not any("военный рекрутинг" in concern for concern in result.concerns)


@pytest.mark.parametrize(
    ("text", "minimum", "maximum", "currency", "period"),
    (
        ("Base pay: $18/hour", "18", "18", "USD", "hour"),
        ("Salary range: EUR 36k-48k annually", "36000", "48000", "EUR", "year"),
        ("Compensation: GBP 2,000 per month", "2000", "2000", "GBP", "month"),
    ),
)
def test_parse_salary(
    text: str,
    minimum: str,
    maximum: str,
    currency: str,
    period: str,
) -> None:
    salary = parse_salary(text)

    assert salary is not None
    assert salary.minimum == Decimal(minimum)
    assert salary.maximum == Decimal(maximum)
    assert salary.currency == currency
    assert salary.period == period


def test_parse_salary_ignores_non_salary_monthly_benefit() -> None:
    assert parse_salary("Benefits include an internet reimbursement of $100/month.") is None


@pytest.mark.asyncio
async def test_the_muse_rejects_malformed_api_response() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"unexpected": []}))
    ) as client:
        source = TheMuseSource(client=client)

        with pytest.raises(TheMuseSourceError, match="results list"):
            _ = [listing async for listing in source.fetch()]


def test_the_muse_normalizer_rejects_non_remote_listing() -> None:
    source = TheMuseSource()
    job = _job(locations=[{"name": "Prague, Czechia"}])
    refs = job["refs"]
    assert isinstance(refs, dict)
    listing = RawListing(
        external_id=str(job["id"]),
        source_url=str(refs["landing_page"]),
        payload=job,
    )

    with pytest.raises(TheMuseSourceError, match="not marked as remote"):
        source.normalize(listing)


def test_registry_builds_the_muse_with_six_hour_interval() -> None:
    settings = Settings(
        _env_file=None,
        djinni_source_enabled=False,
        freelancer_source_enabled=False,
        workua_source_enabled=False,
        jobs_cz_source_enabled=False,
        startupjobs_cz_source_enabled=False,
        prace_cz_source_enabled=False,
        freelance_cz_source_enabled=False,
        startup_jobs_source_enabled=False,
        jobicy_source_enabled=False,
        we_work_remotely_source_enabled=False,
        dou_jobs_source_enabled=False,
        himalayas_source_enabled=False,
        the_muse_source_enabled=True,
        the_muse_api_key="test-key",
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert isinstance(sources[0], TheMuseSource)
    assert sources[0].deactivate_missing_listings is False
    assert settings.source_poll_interval_seconds("the_muse") == 21600
