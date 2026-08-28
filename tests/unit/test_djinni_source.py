import json
from copy import deepcopy
from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.djinni import DjinniSource, DjinniSourceError, parse_job_postings

REMOTE_JOB = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "applicantLocationRequirements": {
        "@type": "AdministrativeArea",
        "address": {
            "@type": "PostalAddress",
            "addressRegion": "Europe",
        },
    },
    "datePosted": "2026-08-22T15:36:45+03:00",
    "description": "Build APIs with Python and Django.",
    "employmentType": "FULL_TIME",
    "estimatedSalary": {
        "@type": "MonetaryAmount",
        "currency": "USD",
        "minValue": 1200,
        "maxValue": 1800,
    },
    "hiringOrganization": {
        "@type": "Organization",
        "name": "Example Company",
    },
    "identifier": 844408,
    "jobLocationType": "TELECOMMUTE",
    "title": "Junior Python Developer",
    "url": "https://djinni.co/jobs/844408-junior-python-developer/",
}

ONSITE_JOB = {
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "identifier": 844409,
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressCountry": "Czechia",
            "addressLocality": "Prague",
        },
    },
    "title": "Office Developer",
    "url": "https://djinni.co/jobs/844409-office-developer/",
}


def _html(*postings: dict[str, object]) -> str:
    payload = json.dumps(list(postings))
    return f'<html><script type="application/ld+json">{payload}</script></html>'


def test_parse_job_postings_ignores_unrelated_json_ld() -> None:
    html = '<script type="application/ld+json">{"@type":"WebSite"}</script>' + _html(
        REMOTE_JOB, ONSITE_JOB
    )

    postings = parse_job_postings(html)

    assert [posting["identifier"] for posting in postings] == [844408, 844409]


@pytest.mark.asyncio
async def test_djinni_source_keeps_only_remote_jobs_and_normalizes_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, text="<html></html>")
        assert request.url == httpx.URL("https://djinni.test/jobs/remote/")
        return httpx.Response(200, text=_html(REMOTE_JOB, ONSITE_JOB))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.test/jobs/remote/",
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert listings[0].external_id == "844408"

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.company == "Example Company"
    assert normalized.location_text == "Europe"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("1200")
    assert normalized.salary_max == Decimal("1800")
    assert normalized.salary_currency == "USD"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T12:36:45+00:00"


@pytest.mark.asyncio
async def test_djinni_source_loads_later_pages_without_duplicate_results() -> None:
    second_job = deepcopy(REMOTE_JOB)
    second_job.update(
        {
            "identifier": 844410,
            "title": "Junior React Developer",
            "url": "https://djinni.co/jobs/844410-junior-react-developer/",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page is None:
            return httpx.Response(200, text=_html(REMOTE_JOB))
        if page == "2":
            return httpx.Response(200, text=_html(REMOTE_JOB, second_job))
        return httpx.Response(200, text="<html></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.test/jobs/remote/?primary_keyword=Python",
            max_pages=3,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["844408", "844410"]


@pytest.mark.asyncio
async def test_djinni_source_reports_missing_structured_data() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html></html>"))
    ) as client:
        source = DjinniSource(client=client)
        with pytest.raises(DjinniSourceError, match="JobPosting JSON-LD"):
            _ = [listing async for listing in source.fetch()]
