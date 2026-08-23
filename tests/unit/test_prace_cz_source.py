import json
from decimal import Decimal

import httpx
import pytest

from jobradar.domain.enums import WorkMode
from jobradar.sources.base import CachedListing
from jobradar.sources.prace_cz import PraceCzSource, parse_prace_cz_cards

REMOTE_ID = "e055f83d-e89f-44f8-90dc-a69dc47b9fc7"
PARTIAL_ID = "066f20c2-abcb-42a4-8980-7af0de812489"

SEARCH_PAGE = rf"""
<html><body>
<script>self.__next_f.push([1,"{{\"metadata\":{{\"advertId\":\"{REMOTE_ID}\"}},
\"advert\":{{\"workLocation\":{{\"type\":\"FULL_REMOTE\"}}}}}}"])</script>
<article id="advert-{REMOTE_ID}">
  <h2><a data-testid="advert-link" href="/nabidka/{REMOTE_ID}/?rps=2077">
    Junior React Developer
  </a></h2>
</article>
<article id="advert-{PARTIAL_ID}">
  <h2><a data-testid="advert-link" href="/nabidka/{PARTIAL_ID}/">
    Office Frontend Developer
  </a></h2>
  <span>Možnost občasné práce z domova</span>
</article>
</body></html>
"""

POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "identifier": {"@type": "PropertyValue", "value": "example"},
    "title": "Junior React Developer",
    "description": "<h2>Role</h2><p>Build React and JavaScript applications.</p>",
    "hiringOrganization": {"@type": "Organization", "name": "Remote Company"},
    "employmentType": ["FULL_TIME", "EMPLOYEE"],
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "CZK",
        "value": {
            "@type": "QuantitativeValue",
            "minValue": 50000,
            "maxValue": 70000,
            "unitText": "MONTH",
        },
    },
    "datePosted": "2026-08-20T10:30:00+02:00",
}

DETAIL_PAGE = (
    '<html><body><script type="application/ld+json">'
    + json.dumps(POSTING)
    + "</script></body></html>"
)


def test_prace_cz_card_parser_distinguishes_full_and_partial_remote() -> None:
    cards = parse_prace_cz_cards(SEARCH_PAGE)

    assert len(cards) == 2
    assert cards[0].external_id == REMOTE_ID
    assert cards[0].fully_remote is True
    assert cards[1].external_id == PARTIAL_ID
    assert cards[1].fully_remote is False


def test_prace_cz_does_not_treat_mostly_from_home_label_as_full_remote() -> None:
    page = f"""
    <article id="advert-{REMOTE_ID}">
      <h2><a data-testid="advert-link" href="/nabidka/{REMOTE_ID}/">
        Junior React Developer
      </a></h2>
      <span>Práce převážně z domova</span>
    </article>
    """

    cards = parse_prace_cz_cards(page)

    assert len(cards) == 1
    assert cards[0].fully_remote is False


@pytest.mark.asyncio
async def test_prace_cz_loads_full_jobposting_only_for_remote_card() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/nabidky/programator/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == f"/nabidka/{REMOTE_ID}/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = PraceCzSource(
            search_urls=("https://www.prace.cz/nabidky/programator/",),
            client=client,
        )
        listings = [listing async for listing in source.fetch()]

    assert requested_paths == [
        "/nabidky/programator/",
        f"/nabidka/{REMOTE_ID}/",
    ]
    assert len(listings) == 1

    normalized = source.normalize(listings[0])
    assert normalized.title == "Junior React Developer"
    assert normalized.company == "Remote Company"
    assert normalized.description == "Role Build React and JavaScript applications."
    assert normalized.work_mode is WorkMode.REMOTE
    assert normalized.employment_type == "full_time"
    assert normalized.salary_min == Decimal("50000")
    assert normalized.salary_max == Decimal("70000")
    assert normalized.salary_currency == "CZK"
    assert normalized.salary_period == "month"


@pytest.mark.asyncio
async def test_prace_cz_reuses_cached_jobposting_for_an_unchanged_card() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/nabidky/programator/":
            return httpx.Response(200, text=SEARCH_PAGE)
        if request.url.path == f"/nabidka/{REMOTE_ID}/":
            return httpx.Response(200, text=DETAIL_PAGE)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = PraceCzSource(
            search_urls=("https://www.prace.cz/nabidky/programator/",),
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
    assert requested_paths.count("/nabidky/programator/") == 2
    assert requested_paths.count(f"/nabidka/{REMOTE_ID}/") == 1
