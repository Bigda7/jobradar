import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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

DEFAULT_READER_BASE_URL = "https://r.jina.ai/http://www.work.ua"
DEFAULT_SEARCH_URLS = (
    "https://www.work.ua/en/jobs-remote-programmer/",
    "https://www.work.ua/en/jobs-remote-developer/",
    "https://www.work.ua/en/jobs-remote-junior+developer/",
    "https://www.work.ua/en/jobs-remote-front-end+developer/",
    "https://www.work.ua/en/jobs-remote-back-end+developer/",
    "https://www.work.ua/en/jobs-remote-full-stack+developer/",
    "https://www.work.ua/en/jobs-remote-python/",
    "https://www.work.ua/en/jobs-remote-django/",
    "https://www.work.ua/en/jobs-remote-fastapi/",
    "https://www.work.ua/en/jobs-remote-react/",
    "https://www.work.ua/en/jobs-remote-javascript/",
    "https://www.work.ua/en/jobs-remote-typescript/",
    "https://www.work.ua/en/jobs-remote-node.js/",
    "https://www.work.ua/en/jobs-remote-shopify/",
)
USER_AGENT = "JobRadar/0.5 (personal job aggregator)"
JOB_PATH_PATTERN = re.compile(r"/(?:en/)?jobs/(?P<id>\d+)/")
NUMBER_PATTERN = re.compile(r"\d+(?:[\s\u00a0\u2009\u202f.,]\d+)*")


class WorkUaSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkUaCard:
    external_id: str
    url: str
    title: str
    company: str | None
    description: str | None
    salary_text: str | None
    location_text: str | None
    published_at: str | None


class WorkUaSource(BaseSource):
    name = "workua"
    display_name = "Work.ua"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        search_urls: tuple[str, ...] = DEFAULT_SEARCH_URLS,
        reader_base_url: str = DEFAULT_READER_BASE_URL,
        request_timeout_seconds: float = 30.0,
        max_pages_per_search: int = 2,
        max_items: int = 100,
        remote_only: bool = True,
        detail_cache_ttl_seconds: int = 86400,
        detail_request_delay_seconds: float = 0.0,
        retry_attempts: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._search_urls = search_urls
        self._reader_base_url = reader_base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._max_pages_per_search = max_pages_per_search
        self._max_items = max_items
        self._remote_only = remote_only
        self._detail_cache_ttl_seconds = detail_cache_ttl_seconds
        self._detail_request_delay_seconds = detail_request_delay_seconds
        self._retry_attempts = retry_attempts
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        yielded = 0
        successful_search_pages = 0
        card_batches: list[list[WorkUaCard]] = []
        for search_url in self._search_urls:
            query_ids: set[str] = set()
            for page_number in range(1, self._max_pages_per_search + 1):
                page_url = _page_url(search_url, page_number)
                self.record_page()
                try:
                    cards = await self._fetch_search_cards(page_url)
                except WorkUaSourceError as error:
                    self.report_warning(str(error))
                    break
                successful_search_pages += 1
                self.record_candidates(len(cards))
                if not cards:
                    break
                new_cards = [card for card in cards if card.external_id not in query_ids]
                if not new_cards:
                    break
                query_ids.update(card.external_id for card in new_cards)
                card_batches.append(new_cards)

        if not card_batches:
            if successful_search_pages:
                raise WorkUaSourceError("Configured Work.ua searches returned no vacancy cards.")
            raise WorkUaSourceError("Every configured Work.ua search page failed.")

        for cards in card_batches:
            for card in cards:
                if card.external_id in seen:
                    continue
                seen.add(card.external_id)
                if self._remote_only and not _is_remote(card.location_text):
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

    async def _fetch_search_cards(self, search_url: str) -> list[WorkUaCard]:
        cards: list[WorkUaCard] = []
        for _ in range(self._retry_attempts):
            html = await self._fetch_page(search_url)
            cards = parse_workua_cards(html)
            if cards:
                break
        return cards

    async def _fetch_description(self, vacancy_url: str) -> str | None:
        try:
            html = await self._fetch_page(vacancy_url)
        except WorkUaSourceError as error:
            if "404" in str(error) or "410" in str(error):
                return None
            raise
        return parse_workua_description(html)

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        salary_min, salary_max, salary_currency = parse_salary(payload.get("salary_text"))
        description = _optional_string(payload.get("description"))
        employment_text = " ".join(
            value
            for value in (
                description,
                _optional_string(payload.get("summary")),
            )
            if value
        )
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_optional_string(payload.get("company")),
            description=description,
            location_text=_optional_string(payload.get("location_text")) or "Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(employment_text),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period="month" if salary_currency is not None else None,
            published_at=_datetime(payload.get("published_at")),
        )

    async def _fetch_page(self, search_url: str) -> str:
        request_url = _reader_url(self._reader_base_url, search_url)
        if self._client is not None:
            return await self._request(self._client, request_url)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/plain",
                "X-Return-Format": "html",
            },
            timeout=timeout,
        ) as client:
            return await self._request(client, request_url)

    async def _request(self, client: httpx.AsyncClient, request_url: str) -> str:
        try:
            response = await get_with_backoff(
                client,
                request_url,
                headers={"X-Return-Format": "html"},
                attempts=self._retry_attempts,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise WorkUaSourceError(f"Work.ua reader request failed: {error}") from error
        return response.text


def _card_payload(card: WorkUaCard) -> dict[str, Any]:
    return {
        "title": card.title,
        "company": card.company,
        "summary": card.description,
        "salary_text": card.salary_text,
        "location_text": card.location_text,
        "published_at": card.published_at,
    }


def _discovery_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "title",
            "company",
            "summary",
            "salary_text",
            "location_text",
            "published_at",
        )
    }


class _WorkUaCardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[WorkUaCard] = []
        self._card_depth = 0
        self._href: str | None = None
        self._title_parts: list[str] = []
        self._company_parts: list[str] = []
        self._description_parts: list[str] = []
        self._salary_parts: list[str] = []
        self._all_parts: list[str] = []
        self._published_at: str | None = None
        self._capture_title = False
        self._capture_company = False
        self._capture_description = False
        self._capture_salary = False
        self._expect_company = False
        self._expect_salary = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value for key, value in attrs}
        classes = set((attributes.get("class") or "").split())
        if tag.casefold() == "div" and "job-link" in classes and self._card_depth == 0:
            self._start_card()
            return
        if self._card_depth == 0:
            return
        if tag.casefold() == "div":
            self._card_depth += 1
        if tag.casefold() == "a":
            href = attributes.get("href") or ""
            if JOB_PATH_PATTERN.search(href) and self._href is None:
                self._href = href
                self._capture_title = True
        elif tag.casefold() == "span":
            title = (attributes.get("title") or "").casefold()
            if title == "company information":
                self._expect_company = True
            elif title == "salary":
                self._expect_salary = True
            if "strong-600" in classes:
                if self._expect_salary and not self._salary_parts:
                    self._capture_salary = True
                elif self._expect_company and not self._company_parts:
                    self._capture_company = True
        elif tag.casefold() == "p" and "ellipsis" in classes:
            self._capture_description = True
        elif tag.casefold() == "time" and self._published_at is None:
            self._published_at = attributes.get("datetime")

    def handle_endtag(self, tag: str) -> None:
        if self._card_depth == 0:
            return
        normalized_tag = tag.casefold()
        if normalized_tag == "a" and self._capture_title:
            self._capture_title = False
        elif normalized_tag == "span":
            if self._capture_salary:
                self._capture_salary = False
                self._expect_salary = False
            elif self._capture_company:
                self._capture_company = False
                self._expect_company = False
        elif normalized_tag == "p" and self._capture_description:
            self._capture_description = False
        if normalized_tag == "div":
            self._card_depth -= 1
            if self._card_depth == 0:
                self._finish_card()

    def handle_data(self, data: str) -> None:
        if self._card_depth == 0:
            return
        value = _clean_text(data)
        if not value:
            return
        self._all_parts.append(value)
        if self._capture_title:
            self._title_parts.append(value)
        if self._capture_company:
            self._company_parts.append(value)
        if self._capture_description:
            self._description_parts.append(value)
        if self._capture_salary:
            self._salary_parts.append(value)

    def _start_card(self) -> None:
        self._card_depth = 1
        self._href = None
        self._title_parts = []
        self._company_parts = []
        self._description_parts = []
        self._salary_parts = []
        self._all_parts = []
        self._published_at = None
        self._capture_title = False
        self._capture_company = False
        self._capture_description = False
        self._capture_salary = False
        self._expect_company = False
        self._expect_salary = False

    def _finish_card(self) -> None:
        href = self._href
        title = _join_parts(self._title_parts)
        if not href or not title:
            return
        match = JOB_PATH_PATTERN.search(href)
        if match is None:
            return
        location = (
            "Remote"
            if any(re.search(r"\bremote\b", part, flags=re.IGNORECASE) for part in self._all_parts)
            else None
        )
        self.cards.append(
            WorkUaCard(
                external_id=match.group("id"),
                url=urljoin("https://www.work.ua", href),
                title=title,
                company=_join_parts(self._company_parts) or None,
                description=_join_parts(self._description_parts) or None,
                salary_text=_join_parts(self._salary_parts) or None,
                location_text=location,
                published_at=self._published_at,
            )
        )


class _WorkUaDescriptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._capture_div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value for key, value in attrs}
        if self._capture_div_depth and normalized_tag == "div":
            self._capture_div_depth += 1
        elif normalized_tag == "div" and attributes.get("id") == "job-description":
            self._capture_div_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture_div_depth and tag.casefold() == "div":
            self._capture_div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_div_depth:
            value = _clean_text(data)
            if value:
                self.parts.append(value)


def parse_workua_cards(html: str) -> list[WorkUaCard]:
    parser = _WorkUaCardParser()
    parser.feed(html)
    return parser.cards


def parse_workua_description(html: str) -> str | None:
    parser = _WorkUaDescriptionParser()
    parser.feed(html)
    return _join_parts(parser.parts) or None


def parse_salary(value: Any) -> tuple[Decimal | None, Decimal | None, str | None]:
    text = _optional_string(value)
    if text is None:
        return None, None, None
    currency = _salary_currency(text)
    if currency is None:
        return None, None, None
    numbers = [_decimal_number(item) for item in NUMBER_PATTERN.findall(text)]
    amounts = [item for item in numbers if item is not None]
    if not amounts:
        return None, None, None
    minimum = amounts[0]
    maximum = amounts[1] if len(amounts) > 1 else amounts[0]
    return minimum, maximum, currency


def _salary_currency(value: str) -> str | None:
    normalized = value.casefold()
    if "uah" in normalized or "грн" in normalized or "₴" in normalized:
        return "UAH"
    if "usd" in normalized or "$" in value:
        return "USD"
    if "czk" in normalized or "kč" in normalized:
        return "CZK"
    if "eur" in normalized or "€" in value:
        return "EUR"
    return None


def _decimal_number(value: str) -> Decimal | None:
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
    if "full-time" in lowered:
        values.append("full_time")
    if "part-time" in lowered:
        values.append("part_time")
    return ",".join(values) or None


def _datetime(value: Any) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _reader_url(reader_base_url: str, search_url: str) -> str:
    target = search_url.replace("https://www.work.ua", "", 1)
    if not target.startswith("/"):
        raise WorkUaSourceError(f"Unsupported Work.ua search URL: {search_url}")
    return f"{reader_base_url}{target}"


def _page_url(search_url: str, page_number: int) -> str:
    if page_number <= 1:
        return search_url
    parsed = urlsplit(search_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _is_remote(value: str | None) -> bool:
    return value is not None and value.casefold() == "remote"


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise WorkUaSourceError(f"Work.ua vacancy is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = _clean_text(str(value))
    return result or None


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _join_parts(parts: list[str]) -> str:
    return _clean_text(" ".join(parts))
