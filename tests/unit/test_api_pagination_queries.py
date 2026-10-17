from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from jobradar.api.app import create_app
from jobradar.db.models import Listing, Opportunity, OpportunityUserState, Source
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource

PAGE_CASES = [("/jobs", "score", None)] + [
    ("/matches", order, source)
    for order in ("score", "newest", "company")
    for source in (None, "djinni")
]


async def verify_page_query_shape(engine, session_factory, path):
    await IngestionService(session_factory).run_source(MockSource())
    await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    queries = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("select"):
            queries.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app(session_factory)), base_url="http://test"
        ) as client:
            response = await client.get(path, params={"limit": 1, "min_score": 0})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert len(queries) == 2
    page_query = queries[1]
    assert ("job_page" if path == "/jobs" else "match_page") in page_query
    assert page_query.count("order by listings.quality_score desc, listings.id asc") == 1
    assert page_query.count("limit") == 1
    assert "row_number() over (partition by listings.opportunity_id" in page_query


@pytest.mark.parametrize("path", ["/jobs", "/matches"])
async def test_api_selects_one_canonical_listing_after_pagination(
    sqlite_engine, sqlite_session_factory, path
):
    await verify_page_query_shape(sqlite_engine, sqlite_session_factory, path)


async def verify_page_results(session_factory, path, order, source_filter):
    values = [
        dict(
            DEFAULT_LISTINGS[0],
            id=f"page-{index}",
            url=f"https://example.com/jobs/page-{index}",
            title=f"Python Developer {index}",
            company=f"Company {index % 3}",
            description=f"Build Python Django PostgreSQL applications. Record {index}. " * 150,
        )
        for index in range(8)
    ]
    await IngestionService(session_factory).run_source(MockSource(values))
    await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    async with session_factory() as session, session.begin():
        opportunities = list(await session.scalars(select(Opportunity).order_by(Opportunity.id)))
        originals = list(await session.scalars(select(Listing).order_by(Listing.id)))
        for index, opportunity in enumerate(opportunities):
            opportunity.published_at = datetime(2026, 9, index + 1, tzinfo=UTC)
        for listing in originals:
            listing.quality_score = 1
        originals[4].is_active = False
        session.add(OpportunityUserState(opportunity_id=opportunities[3].id, disposition="hidden"))
        sources = [
            Source(name="djinni", display_name="Djinni", enabled=True),
            Source(name="disabled-source", display_name="Disabled", enabled=False),
            Source(name="inactive-source", display_name="Inactive", enabled=True),
            Source(name="dou_jobs", display_name="DOU", enabled=True),
        ]
        session.add_all(sources)
        await session.flush()
        for index, opportunity in enumerate(opportunities):
            alternatives = [
                (sources[1], "disabled", True, 90000, "https://example.com/disabled"),
                (sources[2], "inactive", False, 90000, "https://example.com/inactive"),
                (sources[3], "unsafe", True, 90000, "https://jobs.dou.ua.evil.test/vacancies/1/"),
            ]
            if index < 2:
                alternatives.extend(
                    (sources[0], f"tie-{tie}", True, 50000, f"https://djinni.co/jobs/{index}{tie}/")
                    for tie in (1, 2)
                )
            for source, suffix, active, quality, url in alternatives:
                session.add(
                    Listing(
                        source_id=source.id,
                        opportunity_id=opportunity.id,
                        external_id=f"{index}-{suffix}",
                        source_url=url,
                        canonical_url=url,
                        content_hash=f"{index}-{suffix}",
                        raw_data={},
                        quality_score=quality,
                        is_active=active,
                    )
                )
        expected_ids = {
            opportunity.id
            for index, opportunity in enumerate(opportunities)
            if index not in (3, 4) and (source_filter is None or index < 2)
        }
        expected_links = {
            opportunities[index].id: f"https://djinni.co/jobs/{index}1/" for index in (0, 1)
        }
        session.add(
            Opportunity(
                kind="employment",
                canonical_key="no-visible-listing",
                title="No listing",
                work_mode="remote",
            )
        )
    parameters = {"min_score": 0, "limit": 200}
    if path == "/matches":
        parameters["sort"] = order
    if source_filter:
        parameters["source"] = source_filter
    async with AsyncClient(
        transport=ASGITransport(app=create_app(session_factory)), base_url="http://test"
    ) as client:
        response = await client.get(path, params=parameters)
        assert response.status_code == 200
        full = response.json()
        assert full["total"] == len(expected_ids)
        assert {item["id"] for item in full["items"]} == expected_ids
        for item in full["items"]:
            if item["id"] in expected_links:
                assert item["source_url"] == expected_links[item["id"]]
                assert item["source_name"] == "djinni"
                assert item["source_display_name"] == "Djinni"
            assert len(item["description"]) > 7000
        for offset in range(len(expected_ids) + 1):
            page = await client.get(path, params=dict(parameters, limit=1, offset=offset))
            assert page.status_code == 200
            assert page.json()["total"] == full["total"]
            assert page.json()["items"] == full["items"][offset : offset + 1]


@pytest.mark.parametrize("path,order,source_filter", PAGE_CASES)
async def test_pagination_preserves_fields_canonical_ties_and_source_safety(
    sqlite_session_factory, path, order, source_filter
):
    await verify_page_results(sqlite_session_factory, path, order, source_filter)
