from decimal import Decimal

import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate
from jobradar.sources.dou_jobs import (
    DouJobsSource,
    DouJobsSourceError,
    parse_dou_headline,
    parse_salary,
)
from jobradar.sources.registry import build_source_registry

RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>
        Junior Full-Stack Developer (React &amp;amp; Django) в Example Labs,
        $1200–1800, за кордоном, віддалено
      </title>
      <link>https://jobs.dou.ua/companies/example/vacancies/370001/?utm_source=jobsrss</link>
      <description>
        &lt;p&gt;Full-time. Build React interfaces and Django REST APIs.&lt;/p&gt;
      </description>
      <pubDate>Sun, 23 Aug 2026 12:00:00 +0300</pubDate>
      <guid>https://jobs.dou.ua/companies/example/vacancies/370001/?1787486400</guid>
    </item>
    <item>
      <title>Junior Python Developer в Hybrid Labs, віддалено</title>
      <link>https://jobs.dou.ua/companies/hybrid/vacancies/370002/?utm_source=jobsrss</link>
      <description>
        &lt;p&gt;Гібридний формат роботи: два дні на тиждень в офісі.&lt;/p&gt;
      </description>
      <pubDate>Sun, 23 Aug 2026 11:00:00 +0300</pubDate>
    </item>
    <item>
      <title>Junior React Developer в Office Labs, Київ</title>
      <link>https://jobs.dou.ua/companies/office/vacancies/370003/?utm_source=jobsrss</link>
      <description>&lt;p&gt;Office work in Kyiv.&lt;/p&gt;</description>
      <pubDate>Sun, 23 Aug 2026 10:00:00 +0300</pubDate>
    </item>
    <item>
      <title>Junior Python Developer в OM Defence Systems, віддалено</title>
      <link>https://jobs.dou.ua/companies/defence/vacancies/370004/?utm_source=jobsrss</link>
      <description>
        &lt;p&gt;Розробка програмного забезпечення для оборонного сектору.&lt;/p&gt;
      </description>
      <pubDate>Sun, 23 Aug 2026 09:00:00 +0300</pubDate>
    </item>
    <item>
      <title>Duplicate в Example Labs, віддалено</title>
      <link>https://jobs.dou.ua/companies/example/vacancies/370001/?utm_source=jobsrss</link>
      <description>&lt;p&gt;Duplicate URL.&lt;/p&gt;</description>
      <pubDate>Sun, 23 Aug 2026 08:00:00 +0300</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.mark.asyncio
async def test_dou_jobs_source_keeps_strict_remote_jobs_and_normalizes_full_rss() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=RSS))
    ) as client:
        source = DouJobsSource(feed_url="https://jobs.dou.test/feeds/?remote", client=client)
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["370001", "370004"]
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Full-Stack Developer (React & Django)"
    assert normalized.company == "Example Labs"
    assert normalized.description == "Full-time. Build React interfaces and Django REST APIs."
    assert normalized.location_text == "за кордоном, віддалено"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("1200")
    assert normalized.salary_max == Decimal("1800")
    assert normalized.salary_currency == "USD"
    assert normalized.salary_period == "month"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-23T09:00:00+00:00"


@pytest.mark.asyncio
async def test_dou_jobs_source_rejects_xml_entities() -> None:
    malicious_xml = (
        '<!DOCTYPE rss [<!ENTITY payload SYSTEM "file:///etc/passwd">]>'
        "<rss><channel><item><title>&payload;</title></item></channel></rss>"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=malicious_xml))
    ) as client:
        source = DouJobsSource(client=client)

        with pytest.raises(DouJobsSourceError):
            _ = [listing async for listing in source.fetch()]


@pytest.mark.asyncio
async def test_dou_military_listing_reaches_shared_hard_rejection() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=RSS))
    ) as client:
        source = DouJobsSource(client=client)
        listings = [listing async for listing in source.fetch()]

    normalized = source.normalize(listings[1])
    candidate = MatchCandidate(
        kind=OpportunityKind.EMPLOYMENT,
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
        raw_data=listings[1].payload,
    )

    result = score_candidate(candidate, BOHDAN_PROFILE)

    assert result.score == 0
    assert result.reasons == ()
    assert "военный рекрутинг" in result.concerns[0]


@pytest.mark.parametrize(
    ("headline", "expected"),
    (
        (
            "Backend Developer в Product Team в Example Labs, від $1500, віддалено",
            ("Backend Developer в Product Team", "Example Labs", "від $1500, віддалено"),
        ),
        ("Unstructured title", ("Unstructured title", None, None)),
    ),
)
def test_parse_dou_headline_uses_the_last_company_separator(
    headline: str,
    expected: tuple[str, str | None, str | None],
) -> None:
    assert parse_dou_headline(headline) == expected


@pytest.mark.parametrize(
    ("details", "expected"),
    (
        ("$1200–1800, віддалено", (Decimal("1200"), Decimal("1800"), "USD")),
        ("від $1500, віддалено", (Decimal("1500"), None, "USD")),
        ("до 80 000 грн, Київ, віддалено", (None, Decimal("80000"), "UAH")),
        ("Київ, віддалено", (None, None, None)),
    ),
)
def test_parse_dou_salary_requires_an_explicit_currency(
    details: str,
    expected: tuple[Decimal | None, Decimal | None, str | None],
) -> None:
    assert parse_salary(details) == expected


def test_registry_builds_dou_jobs_with_its_poll_interval() -> None:
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
        dou_jobs_source_enabled=True,
        himalayas_source_enabled=False,
        arbeitnow_source_enabled=False,
        remotive_source_enabled=False,
    )

    sources = build_source_registry(settings)

    assert len(sources) == 1
    assert isinstance(sources[0], DouJobsSource)
    assert sources[0].deactivate_missing_listings is False
    assert settings.source_poll_interval_seconds("dou_jobs") == 1800
