from decimal import Decimal

import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import WorkMode
from jobradar.sources.ashby import AshbySource
from jobradar.sources.ats_config import AtsCompany, CompaniesConfigError, load_companies_config
from jobradar.sources.greenhouse import GreenhouseSource
from jobradar.sources.lever import LeverSource
from jobradar.sources.registry import build_source_registry


@pytest.mark.asyncio
async def test_greenhouse_source_keeps_only_strict_remote_jobs() -> None:
    document = {
        "jobs": [
            {
                "id": 101,
                "title": "Junior Full-Stack Developer",
                "location": {"name": "Remote, Europe"},
                "absolute_url": "https://job-boards.greenhouse.io/example/jobs/101",
                "content": (
                    "&lt;p&gt;Full-time. Build React and Django APIs. "
                    "Base salary: USD 24,000 - 36,000 per year.&lt;/p&gt;"
                ),
                "updated_at": "2026-08-24T10:00:00Z",
            },
            {
                "id": 102,
                "title": "React Developer",
                "location": {"name": "Remote; Office Based - London"},
                "absolute_url": "https://job-boards.greenhouse.io/example/jobs/102",
                "content": "Hybrid role.",
                "updated_at": "2026-08-24T09:00:00Z",
            },
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document))
    ) as client:
        source = GreenhouseSource(
            companies=(AtsCompany("Example", "greenhouse", "example"),),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    assert listings[0].external_id == "example:101"
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Full-Stack Developer"
    assert normalized.company == "Example"
    assert normalized.description == (
        "Full-time. Build React and Django APIs. Base salary: USD 24,000 - 36,000 per year."
    )
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("24000")
    assert normalized.salary_max == Decimal("36000")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "year"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-24T10:00:00+00:00"


@pytest.mark.asyncio
async def test_lever_source_uses_structured_remote_and_salary_fields() -> None:
    document = [
        {
            "id": "remote-101",
            "text": "Junior Python Developer",
            "workplaceType": "remote",
            "categories": {
                "location": "Europe - Remote",
                "commitment": "Full Time",
            },
            "descriptionPlain": "Build Django REST APIs and PostgreSQL services.",
            "salaryRange": {
                "currency": "EUR",
                "interval": "year",
                "min": 24000,
                "max": 36000,
            },
            "hostedUrl": "https://jobs.lever.co/example/remote-101",
        },
        {
            "id": "hybrid-102",
            "text": "React Developer",
            "workplaceType": "hybrid",
            "categories": {"location": "Prague"},
            "descriptionPlain": "Hybrid role.",
            "hostedUrl": "https://jobs.lever.co/example/hybrid-102",
        },
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document))
    ) as client:
        source = LeverSource(
            companies=(AtsCompany("Example", "lever", "example"),),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.company == "Example"
    assert normalized.location_text == "Europe - Remote"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("24000")
    assert normalized.salary_max == Decimal("36000")
    assert normalized.salary_currency == "EUR"
    assert normalized.salary_period == "year"


@pytest.mark.asyncio
async def test_ashby_source_maps_compensation_and_rejects_unlisted_jobs() -> None:
    document = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "remote-201",
                "title": "Junior React Developer",
                "location": "Remote, Europe",
                "isRemote": True,
                "workplaceType": "Remote",
                "isListed": True,
                "descriptionPlain": "Build React and TypeScript interfaces.",
                "publishedAt": "2026-08-24T12:00:00+00:00",
                "employmentType": "FullTime",
                "jobUrl": "https://jobs.ashbyhq.com/example/remote-201",
                "compensation": {
                    "summaryComponents": [
                        {
                            "compensationType": "Salary",
                            "interval": "1 YEAR",
                            "currencyCode": "USD",
                            "minValue": 20000,
                            "maxValue": 30000,
                        }
                    ]
                },
            },
            {
                "id": "hidden-202",
                "title": "Hidden Remote Developer",
                "location": "Remote",
                "isRemote": True,
                "workplaceType": "Remote",
                "isListed": False,
                "descriptionPlain": "Unlisted role.",
                "jobUrl": "https://jobs.ashbyhq.com/example/hidden-202",
            },
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=document))
    ) as client:
        source = AshbySource(
            companies=(AtsCompany("Example", "ashby", "example"),),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior React Developer"
    assert normalized.company == "Example"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("20000")
    assert normalized.salary_max == Decimal("30000")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "year"


def test_companies_config_loads_enabled_companies_and_rejects_duplicates(tmp_path) -> None:
    valid_path = tmp_path / "companies.yaml"
    valid_path.write_text(
        """
companies:
  - name: Example Greenhouse
    provider: greenhouse
    identifier: example
  - name: Disabled Lever
    provider: lever
    identifier: disabled
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    assert load_companies_config(valid_path) == (
        AtsCompany("Example Greenhouse", "greenhouse", "example"),
    )

    duplicate_path = tmp_path / "duplicates.yaml"
    duplicate_path.write_text(
        """
companies:
  - name: First
    provider: ashby
    identifier: same
  - name: Second
    provider: ashby
    identifier: SAME
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(CompaniesConfigError, match="duplicates"):
        load_companies_config(duplicate_path)


def test_registry_builds_three_ats_sources_with_daily_interval(tmp_path) -> None:
    config_path = tmp_path / "companies.yaml"
    config_path.write_text(
        """
companies:
  - {name: Greenhouse Company, provider: greenhouse, identifier: greenhouse-company}
  - {name: Lever Company, provider: lever, identifier: lever-company}
  - {name: Ashby Company, provider: ashby, identifier: ashby-company}
""".strip(),
        encoding="utf-8",
    )
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
        the_muse_source_enabled=False,
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
        ats_source_enabled=True,
        ats_companies_file=str(config_path),
    )

    sources = build_source_registry(settings)

    assert [source.name for source in sources] == ["greenhouse", "lever", "ashby"]
    assert all(source.deactivate_missing_listings for source in sources)
    assert all(settings.source_poll_interval_seconds(source.name) == 86400 for source in sources)
