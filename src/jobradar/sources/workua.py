import asyncio
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import structlog

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.security import redact_sensitive_text
from jobradar.sources.base import BaseSource, CachedListing
from jobradar.sources.detail_cache import (
    can_reuse_detail,
    discovery_fingerprint,
    polite_delay,
)
from jobradar.sources.link_policy import SOURCE_LISTING_HOSTS, is_trusted_source_link

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
MARKDOWN_CARD_PATTERN = re.compile(
    r"^## \[(?P<title>.+?)\]\((?P<url>https?://(?:www\.)?work\.ua/(?:en/)?jobs/"
    r'(?P<id>\d+)/)(?:\s+"(?P<label>[^"]*)")?\)\s*$',
    flags=re.MULTILINE,
)
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
MARKDOWN_PUBLISHED_PATTERN = re.compile(
    r"job from (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4})",
    flags=re.IGNORECASE,
)
SECURITY_TEXT_MARKERS = (
    "performing security verification",
    "work.ua має перевірити безпеку",
)
CLOUDFLARE_MARKERS = ("challenges.cloudflare.com", "cf-chl-", *SECURITY_TEXT_MARKERS)
logger = structlog.get_logger(__name__)


class WorkUaSourceError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, reason: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def _failure_reason(error: WorkUaSourceError) -> str:
    if error.status_code is not None:
        return f"HTTP {error.status_code}"
    if error.reason in {"timeout", "transport", "request_budget", "deadline", "challenge"}:
        return error.reason
    if "challenge" in str(error).casefold():
        return "challenge"
    return "unavailable"


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
    allowed_listing_hosts = SOURCE_LISTING_HOSTS["workua"]

    def __init__(
        self,
        search_urls: tuple[str, ...] = DEFAULT_SEARCH_URLS,
        reader_base_url: str = DEFAULT_READER_BASE_URL,
        request_timeout_seconds: float = 30.0,
        max_pages_per_search: int = 2,
        max_items: int = 75,
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
        self._run_deadline = monotonic() + 600.0
        self._network_requests = 0
        self._network_request_limit = (
            2
            * self._retry_attempts
            * (len(self._search_urls) * self._max_pages_per_search + self._max_items)
        )
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
                    logger.warning(
                        "workua_search_page_skipped",
                        search_url=page_url,
                        error=str(error),
                    )
                    self.report_warning(
                        f"Work.ua search page unavailable ({_failure_reason(error)}): "
                        f"{redact_sensitive_text(page_url)[:300]}."
                    )
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
                if not is_trusted_source_link(card.url, self.allowed_listing_hosts):
                    self.record_filtered()
                    self.report_warning(
                        f"Work.ua skipped vacancy {card.external_id} with an unexpected URL."
                    )
                    continue
                if self._remote_only and not _is_remote(card.location_text):
                    self.record_filtered()
                    continue
                discovery_payload = _card_payload(card)
                fingerprint = discovery_fingerprint(discovery_payload)
                cached = self.cached_listing(card.external_id)
                cached_description = _cached_description(cached)
                now = datetime.now(UTC)
                detail_status = "complete"
                detail_error: str | None = None
                description: str | None
                if (
                    cached is not None
                    and cached_description is not None
                    and cached.payload.get("detail_status") not in ("cached", "summary")
                    and can_reuse_detail(
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
                    )
                ):
                    description = cached_description
                    detail_fetched_at = cached.detail_fetched_at
                else:
                    await polite_delay(self._detail_request_delay_seconds)
                    detail_fetched_at = None
                    try:
                        description = await self._fetch_description(card.url)
                    except WorkUaSourceError as error:
                        logger.warning(
                            "workua_detail_fallback",
                            vacancy_url=card.url,
                            error=str(error),
                            status_code=error.status_code,
                        )
                        self.record_detail_failure()
                        detail_error = _failure_reason(error)
                        description = cached_description or card.description
                        detail_status = "cached" if cached_description is not None else "summary"
                        if cached_description is not None and cached is not None:
                            detail_fetched_at = cached.detail_fetched_at
                    else:
                        if description is None:
                            logger.warning(
                                "workua_detail_fallback",
                                vacancy_url=card.url,
                                reason="missing_description",
                            )
                            self.record_detail_failure()
                            detail_error = "missing_description"
                            description = cached_description or card.description
                            detail_status = (
                                "cached" if cached_description is not None else "summary"
                            )
                            if cached_description is not None and cached is not None:
                                detail_fetched_at = cached.detail_fetched_at
                        else:
                            detail_fetched_at = datetime.now(UTC)
                if detail_error is not None:
                    retained = "cached description" if detail_status == "cached" else "summary"
                    self.report_warning(
                        f"Work.ua detail unavailable ({detail_error}): "
                        f"{redact_sensitive_text(card.url)[:300]}; "
                        f"retained {retained}."
                    )
                yield RawListing(
                    external_id=card.external_id,
                    source_url=card.url,
                    payload={
                        **discovery_payload,
                        "description": description,
                        "detail_status": detail_status,
                        "detail_error": detail_error,
                    },
                    detail_fetched_at=detail_fetched_at,
                )
                yielded += 1
                if yielded >= self._max_items:
                    self.mark_limit_reached()
                    return

    async def _fetch_search_cards(self, search_url: str) -> list[WorkUaCard]:
        cards: list[WorkUaCard] = []
        for _ in range(self._retry_attempts):
            html = await self._fetch_page(search_url, response_format="html")
            cards = parse_workua_cards(html)
            trusted_count = sum(
                is_trusted_source_link(card.url, self.allowed_listing_hosts) for card in cards
            )
            if self._response_is_challenge(html, "search", "html", trusted_count):
                break
            if cards:
                return cards

        markdown = await self._fetch_page(
            search_url,
            response_format="markdown",
            no_cache=True,
        )
        cards = parse_workua_markdown_cards(markdown)
        trusted_count = sum(
            is_trusted_source_link(card.url, self.allowed_listing_hosts) for card in cards
        )
        if self._response_is_challenge(markdown, "search", "markdown", trusted_count):
            raise WorkUaSourceError(
                "Work.ua returned a security challenge through the reader.", reason="challenge"
            )
        return cards

    async def _fetch_description(self, vacancy_url: str) -> str | None:
        html = await self._fetch_page(vacancy_url, response_format="html")
        description = parse_workua_description(html)
        if not self._response_is_challenge(html, "detail", "html", int(description is not None)):
            if description is not None:
                return description

        markdown = await self._fetch_page(
            vacancy_url,
            response_format="markdown",
            no_cache=True,
        )
        description = parse_workua_markdown_description(markdown)
        if self._response_is_challenge(
            markdown, "detail", "markdown", int(description is not None)
        ):
            raise WorkUaSourceError(
                "Work.ua vacancy returned a security challenge.", reason="challenge"
            )
        return description

    @staticmethod
    def _response_is_challenge(
        content: str, page_kind: str, response_format: str, parsed_items: int
    ) -> bool:
        challenge = is_workua_challenge(content, has_listing_content=parsed_items > 0)
        logger.info(
            "workua_response_classified",
            page_kind=page_kind,
            response_format=response_format,
            classification="challenge" if challenge else "content" if parsed_items else "empty",
            parsed_items=parsed_items,
        )
        return challenge

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

    async def _fetch_page(
        self,
        search_url: str,
        *,
        response_format: str,
        no_cache: bool = False,
    ) -> str:
        request_url = _reader_url(self._reader_base_url, search_url)
        headers = {"X-Return-Format": response_format}
        if no_cache:
            headers["X-No-Cache"] = "true"
        if self._client is not None:
            return await self._request(self._client, request_url, headers=headers)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/plain",
            },
            timeout=timeout,
        ) as client:
            return await self._request(client, request_url, headers=headers)

    async def _request(
        self,
        client: httpx.AsyncClient,
        request_url: str,
        *,
        headers: dict[str, str],
    ) -> str:
        deadline = min(
            getattr(self, "_run_deadline", math.inf),
            monotonic() + min(120.0, self._request_timeout_seconds * self._retry_attempts + 30),
        )
        for attempt in range(self._retry_attempts):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise WorkUaSourceError("Work.ua request deadline reached.", reason="deadline")
            if getattr(self, "_network_requests", 0) >= getattr(
                self, "_network_request_limit", math.inf
            ):
                raise WorkUaSourceError("Work.ua request budget reached.", reason="request_budget")
            self._network_requests = getattr(self, "_network_requests", 0) + 1
            response: httpx.Response | None = None
            try:
                async with asyncio.timeout(min(self._request_timeout_seconds, remaining)):
                    response = await client.get(
                        request_url,
                        headers=headers,
                        timeout=min(self._request_timeout_seconds, remaining),
                    )
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                failure = WorkUaSourceError(
                    f"Work.ua reader returned HTTP {status}.", status_code=status
                )
                if status not in {429, 500, 502, 503, 504}:
                    raise failure from error
            except (httpx.TimeoutException, TimeoutError):
                failure = WorkUaSourceError("Work.ua reader timed out.", reason="timeout")
            except httpx.TransportError:
                failure = WorkUaSourceError("Work.ua reader transport failed.", reason="transport")
            except httpx.HTTPError as error:
                raise WorkUaSourceError("Work.ua reader response is unavailable.") from error
            if attempt + 1 >= self._retry_attempts:
                raise failure
            delay = float(2**attempt)
            if response is not None and response.headers.get("Retry-After") is not None:
                try:
                    delay = float(response.headers["Retry-After"])
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(response.headers["Retry-After"])
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=UTC)
                        delay = (retry_at - datetime.now(UTC)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        pass
            if not math.isfinite(delay) or delay > 30 or max(0.0, delay) >= deadline - monotonic():
                raise failure
            await asyncio.sleep(max(0.0, delay))
        raise WorkUaSourceError("Work.ua retry attempts exhausted.")


def _cached_description(cached: CachedListing | None) -> str | None:
    if cached is None or cached.detail_fetched_at is None:
        return None
    description = _optional_string(cached.payload.get("description"))
    status = cached.payload.get("detail_status")
    if status == "summary":
        return None
    if status is None and description == _optional_string(cached.payload.get("summary")):
        # Older fallback rows retained a successful timestamp after losing their full text.
        return None
    return description


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


class _WorkUaTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self._hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template"}:
            self._hidden_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"script", "style", "template"}:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._hidden_tags:
            index = len(self._hidden_tags) - 1 - self._hidden_tags[::-1].index(tag)
            del self._hidden_tags[index:]

    def handle_data(self, data: str) -> None:
        if not self._hidden_tags:
            self.visible_parts.append(data)


class _WorkUaCardParser(_WorkUaTextParser):
    def __init__(self) -> None:
        super().__init__()
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
        super().handle_starttag(tag, attrs)
        if self._hidden_tags:
            return
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
        super().handle_endtag(tag)
        if self._hidden_tags:
            return
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
        if self._hidden_tags or self._card_depth == 0:
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
        source_url = urljoin("https://www.work.ua", href)
        location = (
            "Remote"
            if any(re.search(r"\bremote\b", part, flags=re.IGNORECASE) for part in self._all_parts)
            else None
        )
        self.cards.append(
            WorkUaCard(
                external_id=match.group("id"),
                url=source_url,
                title=title,
                company=_join_parts(self._company_parts) or None,
                description=_join_parts(self._description_parts) or None,
                salary_text=_join_parts(self._salary_parts) or None,
                location_text=location,
                published_at=self._published_at,
            )
        )


class _WorkUaDescriptionParser(_WorkUaTextParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._capture_div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if self._hidden_tags:
            return
        normalized_tag = tag.casefold()
        attributes = {key.casefold(): value for key, value in attrs}
        if self._capture_div_depth and normalized_tag == "div":
            self._capture_div_depth += 1
        elif normalized_tag == "div" and attributes.get("id") == "job-description":
            self._capture_div_depth = 1

    def handle_endtag(self, tag: str) -> None:
        super().handle_endtag(tag)
        if self._hidden_tags:
            return
        if self._capture_div_depth and tag.casefold() == "div":
            self._capture_div_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_tags and self._capture_div_depth:
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


def parse_workua_markdown_cards(markdown: str) -> list[WorkUaCard]:
    matches = list(MARKDOWN_CARD_PATTERN.finditer(markdown))
    cards: list[WorkUaCard] = []
    for index, match in enumerate(matches):
        source_url = match.group("url").replace("http://", "https://", 1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block = markdown[match.end() : end]
        lines = [_markdown_text(line) for line in block.splitlines()]
        content_lines = [
            line
            for line in lines
            if line
            and not line.startswith("To save a job")
            and line not in {"Already saved", "Save"}
        ]
        salary_text = next(
            (line for line in content_lines if _salary_currency(line) is not None),
            None,
        )
        location_text = next(
            (line for line in content_lines if line.casefold() == "remote"),
            None,
        )
        ignored = {line for line in (salary_text, location_text) if line is not None}
        company_line = next(
            (
                line
                for line in content_lines
                if line not in ignored
                and not line.casefold().startswith("experience ")
                and not line.casefold().endswith(" ago")
            ),
            None,
        )
        company = company_line.removesuffix(", Agency") if company_line else None
        description = next(
            (
                line
                for line in content_lines
                if line not in ignored
                and line != company_line
                and not line.casefold().startswith("experience ")
                and not line.casefold().endswith(" ago")
            ),
            None,
        )
        cards.append(
            WorkUaCard(
                external_id=match.group("id"),
                url=source_url,
                title=_markdown_text(match.group("title")),
                company=company,
                description=description,
                salary_text=salary_text,
                location_text=location_text,
                published_at=_markdown_published_at(match.group("label")),
            )
        )
    return cards


def parse_workua_markdown_description(markdown: str) -> str | None:
    marker = "## About the job"
    if marker not in markdown:
        return None
    section = markdown.split(marker, 1)[1]
    for end_marker in ("### Key requirements and skills", "## Similar jobs", "Apply now"):
        if end_marker in section:
            section = section.split(end_marker, 1)[0]
    parts = [text for line in section.splitlines() if (text := _markdown_text(line))]
    return _join_parts(parts) or None


def is_workua_challenge(content: str, *, has_listing_content: bool = False) -> bool:
    parser = _WorkUaTextParser()
    parser.feed(content)
    visible_text = _join_parts(parser.visible_parts).casefold()
    if any(marker in visible_text for marker in SECURITY_TEXT_MARKERS):
        return True
    normalized = content.casefold()
    return not has_listing_content and any(marker in normalized for marker in CLOUDFLARE_MARKERS)


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


def _markdown_text(value: str) -> str:
    without_links = MARKDOWN_LINK_PATTERN.sub(r"\1", value)
    without_formatting = re.sub(r"[*_`#]", "", without_links)
    return _clean_text(without_formatting.lstrip("- "))


def _markdown_published_at(value: str | None) -> str | None:
    if value is None:
        return None
    match = MARKDOWN_PUBLISHED_PATTERN.search(value)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group("date"), "%B %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed.isoformat()


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _join_parts(parts: list[str]) -> str:
    return _clean_text(" ".join(parts))
