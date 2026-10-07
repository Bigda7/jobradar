from collections import Counter
from xml.sax.saxutils import escape

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from jobradar.db.models import Listing, Opportunity
from jobradar.ingestion.service import IngestionService
from jobradar.sources import djinni as djinni_module
from jobradar.sources.djinni import DjinniSource


def feed(
    identifiers: range | list[int] = range(0),
    *,
    category: str = "Python",
    uncategorized: tuple[int, ...] = (),
    catalog: tuple[str, ...] = (),
) -> str:
    items = []
    for identifier in identifiers:
        item_category = " " if identifier in uncategorized else category
        items.append(
            f"<item><title>Python developer {identifier}</title>"
            f"<link>https://djinni.co/jobs/{identifier}-python-developer/</link>"
            f"<description>Python remote role {identifier}</description>"
            f"<category>{escape(item_category)}</category></item>"
        )
    return (
        "<rss><channel>"
        + "".join(f"<category>{escape(value)}</category>" for value in catalog)
        + "".join(items)
        + "</channel></rss>"
    )


@pytest.fixture
def fast_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(self: object, seconds: float = 0) -> None:
        return None

    async def allow(self: object, category: str) -> bool:
        return True

    monkeypatch.setattr(djinni_module._RequestLimiter, "wait", no_wait)
    monkeypatch.setattr(DjinniSource, "_wait_for_rss_retry", no_wait)
    monkeypatch.setattr(djinni_module.MemoryRequestBudget, "reserve", allow)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_requests", [2, 512])
async def test_uncategorized_records_get_independent_coverage_without_losing_root_descriptions(
    fast_requests: None, max_requests: int
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        assert request.url.params["employment"] == "remote"
        assert request.url.params["editorial"] == "nonhr"
        if request.url.params.get("primary_keyword"):
            body = feed(range(99))
        elif request.url.params.get("exp_level") == "1y":
            body = feed([99, 100], uncategorized=(99, 100))
        elif "exp_level" in request.url.params:
            body = feed()
        else:
            body = feed(range(100), catalog=("Python",), uncategorized=(99,))
        return httpx.Response(200, text=body)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = DjinniSource(client=client, max_feed_requests=max_requests)
            rows = [row async for row in source.fetch()]
    identifiers = {row.external_id for row in rows}
    assert len(rows) == len(identifiers)
    assert next(row for row in rows if row.external_id == "99").payload["description"]
    assert not any(
        value.strip()
        for value in next(row for row in rows if row.external_id == "99").payload["rss"][
            "categories"
        ]
    )
    if max_requests == 2:
        assert len(requests) == 2 and len(rows) == 100
        assert source.consume_run_metrics().limit_reached and source.consume_warnings()
    else:
        assert len(rows) == 101 and "100" in identifiers
        assert len(requests) == 13
        assert source.consume_warnings() == ()
        assert not source.consume_run_metrics().limit_reached
        assert any(log["event"] == "djinni_rss_uncategorized_coverage" for log in logs)


@pytest.mark.asyncio
async def test_uncategorized_fallback_splits_saturated_experience_by_english(
    fast_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        assert request.url.params["country"] == "CZE"
        assert request.url.params["employment"] == "office"
        category = request.url.params.get("primary_keyword")
        experience = request.url.params.get("exp_level")
        english = request.url.params.get("english_level")
        if category:
            body = feed(range(99))
        elif experience == "1y" and english == "upper":
            body = feed(range(99, 179), category="")
        elif experience == "1y" and english == "fluent":
            body = feed(range(179, 219), category="")
        elif experience == "1y" and english is None:
            body = feed(range(99, 199), category="", catalog=("Python",))
        elif experience is not None:
            body = feed()
        else:
            body = feed(range(100), uncategorized=(99,), catalog=("Python",))
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?employment=office&country=CZE",
            remote_only=False,
            client=client,
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == len({row.external_id for row in rows}) == 219
    assert len(requests) == 21
    assert all("primary_keyword" not in url.params for url in requests if "exp_level" in url.params)
    assert source.consume_warnings() == () and not source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_uncategorized_feed_without_category_catalog_still_gets_partitioned(
    fast_requests: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        experience = request.url.params.get("exp_level")
        identifiers = range(100) if experience is None else range(0)
        if experience == "1y":
            identifiers = range(80)
        elif experience == "2y":
            identifiers = range(80, 150)
        return httpx.Response(200, text=feed(identifiers, category=""))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        rows = [row async for row in source.fetch()]
    assert len(rows) == 150
    assert source.consume_warnings() == () and not source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 502])
async def test_failed_uncategorized_fallback_keeps_descriptions_and_visible_failure(
    fast_requests: None, status: int
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        assert request.url.path == "/jobs/rss/"
        if "exp_level" in request.url.params:
            return httpx.Response(status)
        body = (
            feed(range(99))
            if "primary_keyword" in request.url.params
            else feed(range(100), uncategorized=(99,), catalog=("Python",))
        )
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client, metadata_enabled=True)
        rows = [row async for row in source.fetch()]
    assert len(rows) == 100
    assert all(row.payload["description"] for row in rows)
    assert source.consume_run_metrics().limit_reached
    assert any(f"HTTP {status}" in warning for warning in source.consume_warnings())
    assert len(requests) == (11 if status == 502 else 3)


@pytest.mark.asyncio
async def test_ignored_heading_does_not_prevent_valid_category_consistency_recheck(
    fast_requests: None,
) -> None:
    counts: Counter[str | None] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params.get("primary_keyword")
        counts[category] += 1
        if category == "Python":
            body = feed(range(99) if counts[category] == 1 else range(1, 100))
        else:
            body = feed(range(100), catalog=("Development", "Python"))
        return httpx.Response(200, text=body)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = DjinniSource(client=client)
            rows = [row async for row in source.fetch()]
    assert len(rows) == 100
    assert counts == {None: 1, "Development": 1, "Python": 2}
    assert any(log["event"] == "djinni_rss_consistency_recheck_resolved" for log in logs)
    assert source.consume_warnings() == ()
    assert not source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_unreproduced_uncategorized_record_remains_visible_despite_ignored_heading(
    fast_requests: None,
) -> None:
    counts: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counts[str(request.url)] += 1
        category = request.url.params.get("primary_keyword")
        if category == "Python":
            body = feed(range(99))
        elif "exp_level" in request.url.params:
            body = feed()
        else:
            body = feed(range(100), uncategorized=(99,), catalog=("Development", "Python"))
        return httpx.Response(200, text=body)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = DjinniSource(client=client)
            rows = [row async for row in source.fetch()]
    assert len(rows) == 100 and all(row.payload["description"] for row in rows)
    assert sum(counts.values()) == 26
    assert all(count == 1 for url, count in counts.items() if "Development" in url)
    diagnostic = next(log for log in logs if log["event"] == "djinni_rss_consistency_diagnostic")
    assert diagnostic["remaining_missing_ids"] == ["99"]
    assert diagnostic["recheck_selection"] == "eligible"
    assert diagnostic["rechecks_attempted"] == 12
    assert diagnostic["initial_children_processed"] == 12
    assert source.consume_run_metrics().limit_reached
    assert any("1 parent items" in warning for warning in source.consume_warnings())


@pytest.mark.asyncio
async def test_saturated_uncategorized_leaf_keeps_coverage_limited(
    fast_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        return httpx.Response(200, text=feed(range(100), category=""))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?employment=remote&exp_level=3y&english_level=upper",
            client=client,
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 100 and len(requests) == 1
    assert source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_ignored_filter_in_real_experience_partition_is_not_treated_as_catalog_heading(
    fast_requests: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        experience = request.url.params.get("exp_level")
        if experience == "no_exp":
            body = feed(range(3), category="Java")
        elif experience == "1y":
            body = feed(range(99))
        elif experience == "2y":
            body = feed([99])
        elif experience is not None:
            body = feed()
        else:
            body = feed(range(100), catalog=("Python",))
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(client=client)
        rows = [row async for row in source.fetch()]
    assert len(rows) == 100
    assert source.consume_run_metrics().limit_reached
    assert any(
        "subdivision's configured category filter" in warning
        for warning in source.consume_warnings()
    )


@pytest.mark.asyncio
async def test_uncategorized_fallback_does_not_widen_configured_category_selection(
    fast_requests: None,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url)
        assert (
            request.url.params.get_list("primary_keyword") == ["Python", "Java"]
            or len(request.url.params.get_list("primary_keyword")) == 1
        )
        if "exp_level" in request.url.params:
            assert request.url.params.get_list("primary_keyword") == ["Python", "Java"]
            body = (
                feed([99], uncategorized=(99,))
                if request.url.params["exp_level"] == "1y"
                else feed()
            )
        elif len(request.url.params.get_list("primary_keyword")) == 1:
            category = request.url.params["primary_keyword"]
            body = feed(range(99), category=category)
        else:
            body = feed(range(100), uncategorized=(99,))
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = DjinniSource(
            jobs_url="https://djinni.co/jobs/rss/?primary_keyword=Python&primary_keyword=Java",
            client=client,
        )
        rows = [row async for row in source.fetch()]
    assert len(rows) == 100 and len(requests) == 14
    assert source.consume_warnings() == () and not source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
@pytest.mark.parametrize("max_requests", [40, 512])
async def test_consistency_rechecks_are_round_robin_across_affected_parents(
    fast_requests: None, max_requests: int
) -> None:
    categories = ("Python", "Java", "TypeScript")
    counts: Counter[tuple[str | None, str | None]] = Counter()
    recheck_categories = []

    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.params.get("primary_keyword")
        experience = request.url.params.get("exp_level")
        counts[category, experience] += 1
        if category is None:
            body = feed(range(100), catalog=categories)
        else:
            offset = categories.index(category) * 200
            if experience is None:
                identifiers = range(offset, offset + 100)
            elif experience == "no_exp":
                identifiers = range(offset, offset + 99)
            elif experience == "1y" and counts[category, experience] > 1:
                identifiers = range(offset + 99, offset + 100)
            else:
                identifiers = range(0)
            if experience is not None and counts[category, experience] > 1:
                recheck_categories.append(category)
            body = feed(identifiers, category=category)
        return httpx.Response(200, text=body)

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = DjinniSource(client=client, max_feed_requests=max_requests)
            rows = [row async for row in source.fetch()]
    assert len(rows) == len({row.external_id for row in rows}) == 300
    assert recheck_categories[:3] == list(categories)
    diagnostics = [log for log in logs if log["event"] == "djinni_rss_consistency_diagnostic"]
    assert len(diagnostics) == 3
    assert all(log["rechecks_scheduled"] == 4 for log in diagnostics)
    if max_requests == 40:
        assert len(recheck_categories) == 3
        assert all(log["rechecks_attempted"] == 1 for log in diagnostics)
        assert all(log["stop_reason"] == "request_budget" for log in diagnostics)
        assert source.consume_run_metrics().limit_reached and source.consume_warnings()
    else:
        assert len(recheck_categories) == djinni_module.MAX_CONSISTENCY_RECHECKS == 12
        assert Counter(recheck_categories) == dict.fromkeys(categories, 4)
        assert all(log["remaining_missing_count"] == 0 for log in diagnostics)
        assert source.consume_warnings() == () and not source.consume_run_metrics().limit_reached


@pytest.mark.asyncio
async def test_uncategorized_coverage_is_idempotent_and_does_not_archive_retained_listings(
    fast_requests: None, sqlite_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "primary_keyword" in request.url.params:
            body = feed(range(99))
        elif request.url.params.get("exp_level") == "1y":
            body = feed([99, 100], uncategorized=(99, 100))
        elif "exp_level" in request.url.params:
            body = feed()
        else:
            body = feed(range(100), uncategorized=(99,), catalog=("Python",))
        return httpx.Response(200, text=body)

    service = IngestionService(sqlite_session_factory)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        initial = await service.run_source(DjinniSource(client=client, max_feed_requests=2))
        source = DjinniSource(client=client)
        expanded = await service.run_source(source)
        repeated = await service.run_source(source)
    assert initial.created == 100 and initial.deactivated == 0
    assert initial.limit_reached and initial.errors > 0
    assert expanded.created == 1 and expanded.unchanged == 100
    assert repeated.created == repeated.updated == repeated.deactivated == repeated.errors == 0
    assert repeated.unchanged == 101 and not repeated.limit_reached
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Listing)) == 101
        assert await session.scalar(select(func.count()).select_from(Opportunity)) == 101
        assert (
            await session.scalar(
                select(func.count()).select_from(Listing).where(~Listing.is_active)
            )
            == 0
        )
        listing = await session.scalar(select(Listing).where(Listing.external_id == "99"))
        assert listing is not None and listing.raw_data["description"] == "Python remote role 99"
