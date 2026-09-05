import re
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.detail_cache import (
    can_reuse_detail,
    discovery_fingerprint,
    get_with_backoff,
    polite_delay,
)
from jobradar.sources.structured_data import html_to_text, parse_job_postings

DEFAULT_SEARCH_URLS = (
    "https://www.prace.cz/nabidky/programator/",
    "https://www.prace.cz/nabidky/?q=python",
    "https://www.prace.cz/nabidky/?q=react",
    "https://www.prace.cz/nabidky/?q=javascript",
    "https://www.prace.cz/nabidky/?q=django",
    "https://www.prace.cz/nabidky/?q=shopify",
)
USER_AGENT = "JobRadar/0.9 (personal job aggregator)"
OFFER_PATTERN = re.compile(r"/nabidka/(?P<id>[0-9a-f-]{36})/")
WORK_MODE_PATTERN = re.compile(
    r'\\"advertId\\":\\"(?P<id>[0-9a-f-]{36})\\"'
    r'.{0,4000}?\\"workLocation\\":\{.{0,1500}?'
    r'\\"type\\":\\"(?P<mode>[A-Z_]+)\\"',
    re.DOTALL,
)
FULLY_REMOTE_LABEL = "Full remote"


class PraceCzSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PraceCzCard:
    external_id: str
    url: str
    title: str
    fully_remote: bool


class PraceCzSource(BaseSource):
    name = "prace_cz"
    display_name = "Prace.cz"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        search_urls: tuple[str, ...] = DEFAULT_SEARCH_URLS,
        request_timeout_seconds: float = 30.0,
        max_items: int = 20,
        remote_only: bool = True,
        detail_cache_ttl_seconds: int = 86400,
        detail_request_delay_seconds: float = 0.0,
        retry_attempts: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._search_urls = search_urls
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        self._remote_only = remote_only
        self._detail_cache_ttl_seconds = detail_cache_ttl_seconds
        self._detail_request_delay_seconds = detail_request_delay_seconds
        self._retry_attempts = retry_attempts
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        card_groups: list[list[PraceCzCard]] = []
        for search_url in self._search_urls:
            self.record_page()
            html = await self._fetch_page(search_url)
            cards = parse_prace_cz_cards(html)
            self.record_candidates(len(cards))
            card_groups.append(cards)
        if not any(card_groups):
            raise PraceCzSourceError("Prace.cz search pages did not contain any vacancy cards.")

        seen: set[str] = set()
        yielded = 0
        for card in _round_robin(card_groups):
            if card.external_id in seen:
                continue
            seen.add(card.external_id)
            if self._remote_only and not card.fully_remote:
                self.record_filtered()
                continue
            search_card = _search_card_payload(card)
            fingerprint = discovery_fingerprint(search_card)
            cached = self.cached_listing(card.external_id)
            cached_search_card = cached.payload.get("_search_card") if cached is not None else None
            cached_fingerprint = (
                discovery_fingerprint(dict(cached_search_card))
                if isinstance(cached_search_card, Mapping)
                else None
            )
            now = datetime.now(UTC)
            if cached is not None and can_reuse_detail(
                cached,
                fingerprint=fingerprint,
                cached_fingerprint=cached_fingerprint,
                required_fields=("description",),
                ttl_seconds=self._detail_cache_ttl_seconds,
                now=now,
            ):
                posting = dict(cached.payload)
                detail_fetched_at = cached.detail_fetched_at
            else:
                await polite_delay(self._detail_request_delay_seconds)
                fetched_posting = await self._fetch_posting(card.url)
                if fetched_posting is None:
                    self.record_detail_failure()
                    continue
                posting = fetched_posting
                detail_fetched_at = datetime.now(UTC)
            posting["_search_card"] = search_card
            posting["_remote_label"] = FULLY_REMOTE_LABEL
            yield RawListing(
                external_id=card.external_id,
                source_url=card.url,
                payload=posting,
                detail_fetched_at=detail_fetched_at,
            )
            yielded += 1
            if yielded >= self._max_items:
                self.mark_limit_reached()
                return

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        posting = raw_listing.payload
        description_html = _optional_string(posting.get("description"))
        salary_min, salary_max, salary_currency, salary_period = _salary(posting)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(posting.get("title"), "title"),
            company=_company(posting),
            description=html_to_text(description_html) if description_html else None,
            location_text="Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(posting.get("employmentType")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            published_at=_datetime(posting.get("datePosted")),
        )

    async def _fetch_posting(self, url: str) -> dict[str, Any] | None:
        try:
            html = await self._fetch_page(url)
        except PraceCzSourceError as error:
            if "404" in str(error) or "410" in str(error):
                return None
            raise
        postings = parse_job_postings(html)
        return postings[0] if postings else None

    async def _fetch_page(self, url: str) -> str:
        if self._client is not None:
            return await self._request(self._client, url)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "cs,en;q=0.8",
            },
            timeout=timeout,
        ) as client:
            return await self._request(client, url)

    async def _request(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            response = await get_with_backoff(
                client,
                url,
                attempts=self._retry_attempts,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise PraceCzSourceError(f"Prace.cz request failed: {error}") from error
        return response.text


def _search_card_payload(card: PraceCzCard) -> dict[str, Any]:
    return {
        "title": card.title,
        "fully_remote": card.fully_remote,
    }


class _PraceCzCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[PraceCzCard] = []
        self._inside_card = False
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self._capture_title = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value for key, value in attrs}
        if normalized_tag == "article" and (attributes.get("id") or "").startswith("advert-"):
            self._inside_card = True
            self._href = None
            self._title_parts = []
            self._text_parts = []
            self._capture_title = 0
            return
        if not self._inside_card:
            return
        if self._capture_title:
            self._capture_title += 1
        if normalized_tag == "a" and attributes.get("data-testid") == "advert-link":
            self._href = attributes.get("href")
            self._capture_title = 1

    def handle_endtag(self, tag: str) -> None:
        if not self._inside_card:
            return
        normalized_tag = tag.casefold()
        if normalized_tag == "article":
            self._finish_card()
            return
        if self._capture_title:
            self._capture_title -= 1

    def handle_data(self, data: str) -> None:
        if not self._inside_card:
            return
        value = _clean_text(data)
        if not value:
            return
        self._text_parts.append(value)
        if self._capture_title:
            self._title_parts.append(value)

    def _finish_card(self) -> None:
        href = self._href
        title = _clean_text(" ".join(self._title_parts))
        self._inside_card = False
        if not href or not title:
            return
        match = OFFER_PATTERN.search(href)
        if match is None:
            return
        self.cards.append(
            PraceCzCard(
                external_id=match.group("id"),
                url=f"https://www.prace.cz/nabidka/{match.group('id')}/",
                title=title,
                fully_remote=False,
            )
        )


def parse_prace_cz_cards(html: str) -> list[PraceCzCard]:
    parser = _PraceCzCardParser()
    parser.feed(html)
    work_modes = {
        match.group("id"): match.group("mode") for match in WORK_MODE_PATTERN.finditer(html)
    }
    return [
        PraceCzCard(
            external_id=card.external_id,
            url=card.url,
            title=card.title,
            fully_remote=work_modes.get(card.external_id) == "FULL_REMOTE",
        )
        for card in parser.cards
    ]


def _salary(
    posting: Mapping[str, Any],
) -> tuple[Decimal | None, Decimal | None, str | None, str | None]:
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if not isinstance(salary, Mapping):
        return None, None, None, None
    value = salary.get("value")
    amount = value if isinstance(value, Mapping) else salary
    minimum = _decimal(amount.get("minValue") or amount.get("value"))
    maximum = _decimal(amount.get("maxValue") or amount.get("value"))
    currency = _optional_string(salary.get("currency"))
    period = _optional_string(amount.get("unitText"))
    return (
        minimum,
        maximum,
        currency.upper() if currency else None,
        period.casefold() if period else None,
    )


def _company(posting: Mapping[str, Any]) -> str | None:
    organization = posting.get("hiringOrganization")
    if isinstance(organization, Mapping):
        return _optional_string(organization.get("name"))
    return None


def _employment_type(value: Any) -> str | None:
    items = value if isinstance(value, list) else [value]
    normalized = [str(item).casefold() for item in items if item]
    if "full_time" in normalized:
        return "full_time"
    if "part_time" in normalized:
        return "part_time"
    return ",".join(normalized) or None


def _datetime(value: Any) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise PraceCzSourceError(f"Prace.cz JobPosting is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _clean_text(str(value)) or None


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _round_robin(groups: list[list[PraceCzCard]]) -> Iterable[PraceCzCard]:
    index = 0
    while any(index < len(group) for group in groups):
        for group in groups:
            if index < len(group):
                yield group[index]
        index += 1
