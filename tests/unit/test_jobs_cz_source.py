from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.base import CachedListing
from jobradar.sources.jobs_cz import (
    JobsCzSource,
    JobsCzSourceError,
    parse_jobs_cz_cards,
    parse_jobs_cz_description,
    parse_salary,
)

SEARCH_PAGE = """
<html><body>
<article class="SearchResultCard">
  <h2 data-test-ad-title=""><a data-jobad-id="2000000001"
    href="https://www.jobs.cz/rpd/2000000001/?searchId=test">Python Developer</a></h2>
  <span class="Tag">100% Remote</span>
  <span class="Tag">60 000 – 90 000 Kč</span>
  <footer><ul>
    <li>Remote Labs s.r.o.</li>
    <li data-test="serp-locality">Praha</li>
  </ul></footer>
</article>
<article class="SearchResultCard">
  <h2 data-test-ad-title=""><a data-jobad-id="2000000002"
    href="https://www.jobs.cz/rpd/2000000002/">Office Developer</a></h2>
  <span class="Tag">Možnost občasné práce z domova</span>
  <footer><ul>
    <li>Office Company</li>
    <li data-test="serp-locality">Brno</li>
  </ul></footer>
</article>
</body></html>
"""

DETAIL_PAGE = """
<html><body>
<div data-jobad="body" data-test="jd-body-richtext">
  <h2>About the role</h2>
  <p>Full-time development of Python, Django and React applications.<br>Build REST APIs.</p>
</div>
<div>Unrelated page footer</div>
</body></html>
"""


def test_jobs_cz_html_parser_extracts_cards() -> None:
    cards = parse_jobs_cz_cards(SEARCH_PAGE)

    assert len(cards) == 2
    assert cards[0].external_id == "2000000001"
    assert cards[0].url == "https://www.jobs.cz/rpd/2000000001/"
    assert cards[0].title == "Python Developer"
    assert cards[0].company == "Remote Labs s.r.o."
    assert cards[0].location_text == "Praha"
    assert cards[0].salary_text == "60 000 – 90 000 Kč"
    assert cards[0].arrangement == "100% Remote"
    assert cards[1].arrangement == "Možnost občasné práce z domova"


def test_jobs_cz_detail_parser_extracts_only_vacancy_body() -> None:
    description = parse_jobs_cz_description(DETAIL_PAGE)

    assert description is not None
    assert "Python, Django and React" in description
    assert "Build REST APIs" in description
    assert "Unrelated page footer" not in description


def test_jobs_cz_salary_parser_handles_czk_range() -> None:
    assert parse_salary("60 000 – 90 000 Kč") == (
        Decimal("60000"),
        Decimal("90000"),
        "CZK",
    )


@pytest.mark.asyncio
async def test_jobs_cz_source_keeps_only_fully_remote_jobs_and_normalizes() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/prace/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/rpd/2000000001/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = JobsCzSource(
            search_urls=("https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote",),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert requested_urls == [
        "https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote",
        "https://www.jobs.cz/rpd/2000000001/",
    ]
    assert len(listings) == 1
    normalized = source.normalize(listings[0])
    assert normalized.title == "Python Developer"
    assert normalized.company == "Remote Labs s.r.o."
    assert normalized.description is not None
    assert "Build REST APIs" in normalized.description
    assert normalized.location_text == "Remote"
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("60000")
    assert normalized.salary_max == Decimal("90000")
    assert normalized.salary_currency == "CZK"
    assert normalized.salary_period == "month"


@pytest.mark.asyncio
async def test_jobs_cz_reuses_cached_detail_for_an_unchanged_card() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/prace/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/rpd/2000000001/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = JobsCzSource(
            search_urls=("https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote",),
            client=client,
        )
        first = [listing async for listing in source.fetch()]
        source.prime_listing_cache(
            {
                first[0].external_id: CachedListing(
                    payload=first[0].payload,
                    detail_fetched_at=first[0].detail_fetched_at,
                )
            }
        )
        second = [listing async for listing in source.fetch()]

    assert len(second) == 1
    assert requested_paths.count("/prace/") == 2
    assert requested_paths.count("/rpd/2000000001/") == 1


@pytest.mark.asyncio
async def test_jobs_cz_rejects_hybrid_label_even_when_title_claims_full_remote() -> None:
    hybrid_page = SEARCH_PAGE.replace("100% Remote", "Práce převážně z domova").replace(
        "Python Developer",
        "Python Developer - Full Remote",
    )
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, text=hybrid_page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = JobsCzSource(
            search_urls=("https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote",),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert listings == []
    assert requested_paths == ["/prace/"]


@pytest.mark.asyncio
async def test_jobs_cz_source_allows_one_empty_search_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q[0]") == "Shopify":
            return httpx.Response(200, text="<html><body>No matching jobs</body></html>")
        if request.url.path == "/prace/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == "/rpd/2000000001/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = JobsCzSource(
            search_urls=(
                "https://www.jobs.cz/prace/?q%5B0%5D=Shopify",
                "https://www.jobs.cz/prace/?q%5B0%5D=Python",
            ),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert len(listings) == 1


@pytest.mark.asyncio
async def test_jobs_cz_source_rejects_empty_results_from_every_query() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="<html><body>No matching jobs</body></html>")
        )
    ) as client:
        source = JobsCzSource(
            search_urls=("https://www.jobs.cz/prace/?q%5B0%5D=Shopify",),
            client=client,
        )
        with pytest.raises(JobsCzSourceError, match="did not contain any vacancy cards"):
            _ = [listing async for listing in source.fetch()]
