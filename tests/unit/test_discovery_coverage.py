from dataclasses import replace
from urllib.parse import parse_qs, unquote_plus, urlsplit

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.config import Settings
from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.ingestion.service import IngestionService
from jobradar.matching.models import MatchCandidate
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.scorer import score_candidate
from jobradar.sources.discovery import (
    DISCOVERY_TERMS,
    DOU_ADDITIONAL_FEEDS,
    ROBOTA_SEARCH_URLS,
    WORKUA_SEARCH_URLS,
    search_batch,
)
from jobradar.sources.djinni import DjinniSource
from jobradar.sources.mock import MockSource
from jobradar.sources.registry import build_source_registry
from jobradar.sources.workua import WorkUaSource


@pytest.mark.parametrize("skill", [skill for skill in BOHDAN_PROFILE.skills if skill.discovery])
def test_each_discovery_skill_has_a_search_on_every_keyword_based_source(skill) -> None:
    term = skill.aliases[0]
    assert term in DISCOVERY_TERMS
    assert any(term in unquote_plus(url) for url in WORKUA_SEARCH_URLS)
    assert any(term.replace(" ", "-") in unquote_plus(url) for url in ROBOTA_SEARCH_URLS)
    assert any(
        parse_qs(urlsplit(url).query).get("search") == [term] for url in DOU_ADDITIONAL_FEEDS
    )


def test_legacy_environment_does_not_silently_narrow_portfolio_discovery() -> None:
    old = "https://www.work.ua/en/jobs-remote-python/"
    settings = Settings(
        _env_file=None,
        workua_search_urls=old,
        robota_ua_search_urls="https://robota.ua/zapros/python-remote/ukraine",
    )
    assert settings.workua_urls == WORKUA_SEARCH_URLS
    assert settings.robota_ua_urls == ROBOTA_SEARCH_URLS
    assert len(settings.dou_additional_urls) == len(DISCOVERY_TERMS) + 1
    assert settings.workua_max_searches_per_run == 5
    assert settings.robota_ua_max_searches_per_run == 7


def test_custom_mode_preserves_intentional_narrowing_and_deduplicates() -> None:
    url = "https://www.work.ua/en/jobs-remote-python/"
    settings = Settings(
        _env_file=None,
        source_search_profile="custom",
        workua_search_urls=f"{url}; {url}",
        robota_ua_search_urls=ROBOTA_SEARCH_URLS[0],
    )
    assert settings.workua_urls == (url,)
    assert settings.dou_additional_urls == ()
    assert settings.robota_ua_urls == (ROBOTA_SEARCH_URLS[0],)


def test_old_narrow_dou_feed_cannot_replace_broad_root_or_prague_in_portfolio_mode() -> None:
    narrow = "https://jobs.dou.ua/vacancies/feeds/?remote=&search=python"
    settings = Settings(_env_file=None, dou_jobs_feed_url=narrow)
    assert settings.dou_urls[0] == "https://jobs.dou.ua/vacancies/feeds/?remote"
    assert "city=" in settings.dou_urls[1]
    assert narrow in settings.dou_urls[2:]


@pytest.mark.parametrize(
    "url",
    [
        "http://jobs.dou.ua/vacancies/feeds/",
        "https://private.invalid/vacancies/feeds/",
        "https://jobs.dou.ua/vacancies/",
        "https://user:password@jobs.dou.ua/vacancies/feeds/",
    ],
)
def test_dou_additional_feed_settings_reject_non_official_endpoints(url: str) -> None:
    with pytest.raises(ValueError, match="official HTTPS"):
        Settings(_env_file=None, dou_jobs_additional_feed_urls=url)


@pytest.mark.parametrize(
    "urls,size,pinned",
    [
        (WORKUA_SEARCH_URLS, 5, 1),
        (ROBOTA_SEARCH_URLS, 7, 1),
        (("https://jobs.dou.ua/vacancies/feeds/?remote", *DOU_ADDITIONAL_FEEDS), 8, 2),
    ],
)
def test_bounded_rotation_visits_every_search_and_keeps_broad_roots(urls, size, pinned) -> None:
    batches = [search_batch(urls, size, index, pinned=pinned) for index in range(len(urls))]
    assert set().union(*map(set, batches)) == set(urls)
    for batch in batches:
        assert len(batch) == len(set(batch)) == size
        assert batch[:pinned] == urls[:pinned]


@pytest.mark.parametrize(
    "title,description",
    [
        ("Junior Full Stack Developer", "React TypeScript Node.js Express Django FastAPI Next.js"),
        ("Junior Python Developer", "Python Django REST APIs PostgreSQL"),
        ("Junior AI Application Developer", "Python FastAPI LLM API integration"),
    ],
)
def test_discovery_metadata_does_not_change_scores(title: str, description: str) -> None:
    candidate = MatchCandidate(
        OpportunityKind.EMPLOYMENT,
        title,
        "Example",
        description,
        "Worldwide",
        WorkMode.REMOTE,
        "full_time",
        None,
        None,
        None,
        None,
        None,
        {},
    )
    prior = replace(
        BOHDAN_PROFILE,
        skills=tuple(replace(skill, discovery=False) for skill in BOHDAN_PROFILE.skills),
    )
    assert score_candidate(candidate, BOHDAN_PROFILE) == score_candidate(candidate, prior)


@pytest.mark.asyncio
async def test_query_rotation_survives_adapter_recreation_and_other_source_runs(
    sqlite_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = []

    async def empty(self, url):
        observed.append(url)
        return []

    monkeypatch.setattr(WorkUaSource, "_fetch_search_cards", empty)
    service = IngestionService(sqlite_session_factory)
    urls = tuple(f"https://www.work.ua/en/jobs-remote-term{i}/" for i in range(7))
    for index in range(3):
        source = WorkUaSource(search_urls=urls, max_pages_per_search=1, max_searches_per_run=3)
        await service.run_source(source)
        assert observed[index * 3 : (index + 1) * 3] == list(search_batch(urls, 3, index, pinned=1))
        await service.run_source(MockSource())
    assert set(observed) == set(urls)


@pytest.mark.asyncio
async def test_djinni_remains_category_wide_not_a_keyword_allowlist() -> None:
    categories = ("Next.js", "Node.js", "Fullstack", "AI Apps", "Unlisted Category")
    items = "".join(
        f"<item><title>Developer {i}</title>"
        f"<link>https://djinni.co/jobs/{900000 + i}-developer/</link>"
        f"<description>Remote developer</description><category>{category}</category></item>"
        for i, category in enumerate(categories)
    )
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, text=f"<rss><channel>{items}</channel></rss>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(jobs_url="https://djinni.co/jobs/l-nonhr/remote/", client=client)
        rows = [row async for row in source.fetch()]
    assert len(rows) == len(categories)
    assert "primary_keyword" not in calls[0].params
    assert calls[0].path == "/jobs/rss/"
    assert calls[0].params["employment"] == "remote"


def test_registry_wires_search_and_feed_budgets_without_changing_polling() -> None:
    settings = Settings(_env_file=None)
    sources = {source.name: source for source in build_source_registry(settings)}
    assert sources["workua"]._max_searches_per_run == 5
    assert sources["robota_ua"]._max_searches_per_run == 7
    assert sources["dou_jobs"]._max_feeds_per_run == 8
    assert len(sources["dou_jobs"]._feed_urls) == 13
    assert sources["dou_jobs"]._local_city_aliases == BOHDAN_PROFILE.local_city_aliases
    assert settings.workua_poll_interval_seconds == 21600
    assert settings.robota_ua_poll_interval_seconds == 21600
    assert settings.dou_jobs_poll_interval_seconds == 1800
