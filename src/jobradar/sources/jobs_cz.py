import re
from collections.abc import AsyncIterator, Iterable
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

DEFAULT_SEARCH_URLS = (
    "https://www.jobs.cz/prace/?q%5B0%5D=Python%20remote",
    "https://www.jobs.cz/prace/?q%5B0%5D=React%20remote",
    "https://www.jobs.cz/prace/?q%5B0%5D=JavaScript%20remote",
    "https://www.jobs.cz/prace/?q%5B0%5D=Shopify%20remote",
)
USER_AGENT = "JobRadar/0.8 (personal job aggregator)"
JOB_ID_PATTERN = re.compile(r"/rpd/(?P<id>\d+)/")
NUMBER_PATTERN = re.compile(r"\d+(?:[\s\u00a0\u2009\u202f.,]\d+)*")
STRICT_REMOTE_LABELS = {
    "100% remote",
    "full remote",
    "fully remote",
    "remote only",
    "práce pouze z domova",
    "výhradně z domova",
}
HYBRID_REMOTE_LABELS = {
    "práce převážně z domova",
    "work mostly from home",
    "možnost občasné práce z domova",
    "občasné práce z domova",
    "occasional work from home",
    "hybrid",
    "hybridní",
}
REMOTE_LABELS = STRICT_REMOTE_LABELS | HYBRID_REMOTE_LABELS
STRICT_REMOTE_PATTERN = re.compile(
    r"(?:100\s*%\s*remote|full(?:y)?[-\s]+remote|remote[-\s]+only|"
    r"práce\s+pouze\s+z\s+domova|výhradně\s+z\s+domova)",
    re.IGNORECASE,
)
HYBRID_REMOTE_PATTERN = re.compile(
    r"(?:work\s+mostly\s+from\s+home|práce\s+převážně\s+z\s+domova|"
    r"(?:možnost\s+)?občasné\s+práce\s+z\s+domova|"
    r"occasional\s+work\s+from\s+home|\bhybrid(?:ní)?\b)",
    re.IGNORECASE,
)


class JobsCzSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JobsCzCard:
    external_id: str
    url: str
    title: str
    company: str | None
    location_text: str | None
    salary_text: str | None
    arrangement: str | None


class JobsCzSource(BaseSource):
    name = "jobs_cz"
    display_name = "Jobs.cz"
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
        card_groups: list[list[JobsCzCard]] = []
        for search_url in self._search_urls:
            self.record_page()
            html = await self._fetch_page(search_url)
            cards = parse_jobs_cz_cards(html)
            self.record_candidates(len(cards))
            card_groups.append(cards)
        if not any(card_groups):
            raise JobsCzSourceError("Jobs.cz search pages did not contain any vacancy cards.")

        seen: set[str] = set()
        yielded = 0
        for card in _round_robin(card_groups):
            if card.external_id in seen:
                continue
            seen.add(card.external_id)
            if self._remote_only and (
                _is_hybrid_remote(card.arrangement)
                or not _has_strict_remote_marker(card.title, card.arrangement)
            ):
                self.record_filtered()
                continue

            discovery_payload = _card_payload(card)
            fingerprint = discovery_fingerprint(discovery_payload)
            cached = self.cached_listing(card.external_id)
            now = datetime.now(UTC)
            if cached is not None and can_reuse_detail(
                cached,
                fingerprint=fingerprint,
                cached_fingerprint=(
                    discovery_fingerprint(_discovery_payload(cached.payload))
                    if cached is not None
                    else None
                ),
                required_fields=("description",),
                ttl_seconds=self._detail_cache_ttl_seconds,
                now=now,
            ):
                description = _optional_string(cached.payload.get("description"))
                detail_fetched_at = cached.detail_fetched_at
            else:
                await polite_delay(self._detail_request_delay_seconds)
                description = await self._fetch_description(card.url)
                if description is None:
                    self.record_detail_failure()
                    continue
                detail_fetched_at = datetime.now(UTC)
            if description is None:
                continue
            if self._remote_only and (
                _has_hybrid_marker(description)
                or not _has_strict_remote_marker(card.title, card.arrangement, description)
            ):
                self.record_filtered()
                continue
            yield RawListing(
                external_id=card.external_id,
                source_url=card.url,
                payload={**discovery_payload, "description": description},
                detail_fetched_at=detail_fetched_at,
            )
            yielded += 1
            if yielded >= self._max_items:
                self.mark_limit_reached()
                return

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        description = _optional_string(payload.get("description"))
        salary_min, salary_max, salary_currency = parse_salary(payload.get("salary_text"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_optional_string(payload.get("company")),
            description=description,
            location_text="Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(description),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period="month" if salary_currency is not None else None,
        )

    async def _fetch_description(self, vacancy_url: str) -> str | None:
        try:
            html = await self._fetch_page(vacancy_url)
        except JobsCzSourceError as error:
            if "404" in str(error) or "410" in str(error):
                return None
            raise
        return parse_jobs_cz_description(html)

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
            raise JobsCzSourceError(f"Jobs.cz request failed: {error}") from error
        return response.text


def _card_payload(card: JobsCzCard) -> dict[str, Any]:
    return {
        "title": card.title,
        "company": card.company,
        "location_text": card.location_text,
        "salary_text": card.salary_text,
        "arrangement": card.arrangement,
    }


def _discovery_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in ("title", "company", "location_text", "salary_text", "arrangement")
    }


class _JobsCzCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[JobsCzCard] = []
        self._inside_card = False
        self._href: str | None = None
        self._external_id: str | None = None
        self._title_parts: list[str] = []
        self._company_parts: list[str] = []
        self._location_parts: list[str] = []
        self._tag_parts: list[list[str]] = []
        self._capture_title = 0
        self._capture_company = 0
        self._capture_location = 0
        self._capture_tag = 0
        self._inside_footer = False
        self._footer_list_item_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value for key, value in attrs}
        classes = set((attributes.get("class") or "").split())
        if normalized_tag == "article" and "SearchResultCard" in classes:
            self._start_card()
            return
        if not self._inside_card:
            return

        self._increment_captures()
        if normalized_tag == "a":
            external_id = attributes.get("data-jobad-id")
            href = attributes.get("href")
            if external_id and href and self._external_id is None:
                self._external_id = external_id
                self._href = href
        elif normalized_tag == "h2" and "data-test-ad-title" in attributes:
            self._capture_title = 1
        elif normalized_tag == "footer":
            self._inside_footer = True
            self._footer_list_item_count = 0
        elif normalized_tag == "li" and self._inside_footer:
            self._footer_list_item_count += 1
            if attributes.get("data-test") == "serp-locality":
                self._capture_location = 1
            elif self._footer_list_item_count == 1:
                self._capture_company = 1
        elif normalized_tag == "span" and "Tag" in classes:
            self._tag_parts.append([])
            self._capture_tag = 1

    def handle_endtag(self, tag: str) -> None:
        if not self._inside_card:
            return
        normalized_tag = tag.casefold()
        if normalized_tag == "article":
            self._finish_card()
            return
        if normalized_tag == "footer":
            self._inside_footer = False
        self._decrement_captures()

    def handle_data(self, data: str) -> None:
        if not self._inside_card:
            return
        value = _clean_text(data)
        if not value:
            return
        if self._capture_title:
            self._title_parts.append(value)
        if self._capture_company:
            self._company_parts.append(value)
        if self._capture_location:
            self._location_parts.append(value)
        if self._capture_tag and self._tag_parts:
            self._tag_parts[-1].append(value)

    def _start_card(self) -> None:
        self._inside_card = True
        self._href = None
        self._external_id = None
        self._title_parts = []
        self._company_parts = []
        self._location_parts = []
        self._tag_parts = []
        self._capture_title = 0
        self._capture_company = 0
        self._capture_location = 0
        self._capture_tag = 0
        self._inside_footer = False
        self._footer_list_item_count = 0

    def _finish_card(self) -> None:
        href = self._href
        external_id = self._external_id
        title = _join_parts(self._title_parts)
        tags = [_join_parts(parts) for parts in self._tag_parts]
        self._inside_card = False
        if not href or not external_id or not title:
            return
        match = JOB_ID_PATTERN.search(href)
        if match is None or match.group("id") != external_id:
            return
        arrangement = next((tag for tag in tags if tag.casefold() in REMOTE_LABELS), None)
        salary_text = next((tag for tag in tags if _salary_currency(tag) is not None), None)
        self.cards.append(
            JobsCzCard(
                external_id=external_id,
                url=f"https://www.jobs.cz/rpd/{external_id}/",
                title=title,
                company=_join_parts(self._company_parts) or None,
                location_text=_join_parts(self._location_parts) or None,
                salary_text=salary_text,
                arrangement=arrangement,
            )
        )

    def _increment_captures(self) -> None:
        if self._capture_title:
            self._capture_title += 1
        if self._capture_company:
            self._capture_company += 1
        if self._capture_location:
            self._capture_location += 1
        if self._capture_tag:
            self._capture_tag += 1

    def _decrement_captures(self) -> None:
        if self._capture_title:
            self._capture_title -= 1
        if self._capture_company:
            self._capture_company -= 1
        if self._capture_location:
            self._capture_location -= 1
        if self._capture_tag:
            self._capture_tag -= 1


class _JobsCzDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value for key, value in attrs}
        normalized_tag = tag.casefold()
        if self._capture_depth and normalized_tag == "div":
            self._capture_depth += 1
        elif normalized_tag == "div" and attributes.get("data-jobad") == "body":
            self._capture_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth and tag.casefold() == "div":
            self._capture_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_depth:
            value = _clean_text(data)
            if value:
                self.parts.append(value)


def parse_jobs_cz_cards(html: str) -> list[JobsCzCard]:
    parser = _JobsCzCardParser()
    parser.feed(html)
    return parser.cards


def parse_jobs_cz_description(html: str) -> str | None:
    parser = _JobsCzDescriptionParser()
    parser.feed(html)
    return _join_parts(parser.parts) or None


def parse_salary(value: Any) -> tuple[Decimal | None, Decimal | None, str | None]:
    text = _optional_string(value)
    if text is None:
        return None, None, None
    currency = _salary_currency(text)
    if currency is None:
        return None, None, None
    amounts = [amount for item in NUMBER_PATTERN.findall(text) if (amount := _decimal(item))]
    if not amounts:
        return None, None, None
    return amounts[0], amounts[1] if len(amounts) > 1 else amounts[0], currency


def _round_robin(groups: list[list[JobsCzCard]]) -> Iterable[JobsCzCard]:
    index = 0
    while any(index < len(group) for group in groups):
        for group in groups:
            if index < len(group):
                yield group[index]
        index += 1


def _salary_currency(value: str) -> str | None:
    lowered = value.casefold()
    if "czk" in lowered or "kč" in lowered:
        return "CZK"
    if "eur" in lowered or "€" in value:
        return "EUR"
    if "usd" in lowered or "$" in value:
        return "USD"
    return None


def _decimal(value: str) -> Decimal | None:
    normalized = re.sub(r"[\s\u00a0\u2009\u202f]", "", value).replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def _employment_type(description: str | None) -> str | None:
    if not description:
        return None
    lowered = description.casefold()
    values: list[str] = []
    if any(value in lowered for value in ("full-time", "plný úvazek", "plný úväzok")):
        values.append("full_time")
    if any(value in lowered for value in ("part-time", "částečný úvazek", "zkrácený úvazek")):
        values.append("part_time")
    return ",".join(values) or None


def _has_strict_remote_marker(*values: str | None) -> bool:
    return any(value and STRICT_REMOTE_PATTERN.search(value) for value in values)


def _is_hybrid_remote(value: str | None) -> bool:
    return value is not None and value.casefold() in HYBRID_REMOTE_LABELS


def _has_hybrid_marker(value: str) -> bool:
    return HYBRID_REMOTE_PATTERN.search(value) is not None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise JobsCzSourceError(f"Jobs.cz vacancy is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return _clean_text(str(value)) or None


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _join_parts(parts: list[str]) -> str:
    return _clean_text(" ".join(parts))
