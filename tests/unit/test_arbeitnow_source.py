import httpx
import pytest

from jobradar.config import Settings
from jobradar.domain.enums import WorkMode
from jobradar.sources.arbeitnow import ArbeitnowSource
from jobradar.sources.registry import build_source_registry
from jobradar.sources.remotive import RemotiveSource


def _job(slug: str, *, remote: bool = True) -> dict[str, object]:
    return {
        "slug": slug,
        "company_name": "Example GmbH",
        "title": "Junior Python Developer",
        "description": "<p>Build Django APIs and React interfaces.</p>",
        "remote": remote,
        "url": f"https://arbeitnow.test/jobs/{slug}",
        "tags": ["Python", "Remote"],
        "job_types": ["Junior", "fulltime permanent"],
        "location": "Homeoffice",
        "created_at": 1787392800,
    }


@pytest.mark.asyncio
async def test_arbeitnow_source_paginates_and_keeps_only_explicit_remote_jobs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params["page"] == "2":
            return httpx.Response(
                200,
                json={
                    "data": [_job("remote-two")],
                    "links": {"next": None},
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [_job("remote-one"), _job("hybrid", remote=False)],
                "links": {"next": "https://arbeitnow.test/api/job-board-api?page=2"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = ArbeitnowSource(
            api_url="https://arbeitnow.test/api/job-board-api",
            max_pages=3,
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(requests) == 2
    assert [listing.external_id for listing in listings] == ["remote-one", "remote-two"]
    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior Python Developer"
    assert normalized.company == "Example GmbH"
    assert normalized.description == "Build Django APIs and React interfaces."
    assert normalized.location_text == "Homeoffice"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "junior,fulltime_permanent"
    assert normalized.published_at is not None
    assert normalized.published_at.isoformat() == "2026-08-22T10:00:00+00:00"


@pytest.mark.asyncio
async def test_arbeitnow_source_respects_remote_item_limit() -> None:
    payload = {
        "data": [_job("one"), _job("two"), _job("three")],
        "links": {"next": "https://arbeitnow.test/api/job-board-api?page=2"},
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        source = ArbeitnowSource(max_items=2, client=client)
        listings = [listing async for listing in source.fetch()]

    assert [listing.external_id for listing in listings] == ["one", "two"]


def test_registry_builds_new_public_api_sources_with_safe_intervals() -> None:
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
        arbeitnow_source_enabled=True,
        remotive_source_enabled=True,
    )

    sources = build_source_registry(settings)

    assert len(sources) == 2
    assert isinstance(sources[0], ArbeitnowSource)
    assert isinstance(sources[1], RemotiveSource)
    assert settings.source_poll_interval_seconds("arbeitnow") == 21600
    assert settings.source_poll_interval_seconds("remotive") == 21600
