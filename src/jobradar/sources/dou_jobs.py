import hashlib
import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.domain.normalization import normalize_text
from jobradar.http_download import DownloadBudget, bounded_get
from jobradar.sources.base import BaseSource
from jobradar.sources.detail_cache import polite_delay
from jobradar.sources.discovery import search_batch
from jobradar.sources.link_policy import SOURCE_LISTING_HOSTS, is_trusted_source_link
from jobradar.sources.structured_data import html_to_text

DEFAULT_FEED_URL = "https://jobs.dou.ua/vacancies/feeds/?remote"
USER_AGENT = "JobRadar/1.4 (personal job aggregator)"
MAX_FEED_BYTES = 5_000_000

REMOTE_HEADLINE_PATTERN = re.compile(r"(?<!\w)(?:віддалено|remote)(?!\w)", re.IGNORECASE)
HYBRID_WORK_PATTERN = re.compile(
    r"(?<!\w)(?:"
    r"гібридн\w*\s+(?:формат\w*|графік\w*|режим\w*|модел\w*|робот\w*)|"
    r"гибридн\w*\s+(?:формат\w*|график\w*|режим\w*|модел\w*|работ\w*)|"
    r"hybrid[-\s]+(?:work\w*|format|mode|model|schedule)|"
    r"(?:work\w*|format|mode|model|schedule)[-\s]+hybrid|"
    r"частков\w*\s+віддален\w*|частичн\w*\s+удален\w*|"
    r"part(?:ial|ially)[-\s]+remote|"
    r"(?:one|two|three|four|five|\d+)\s+days?\s+(?:per\s+week\s+)?"
    r"(?:in|at)\s+(?:the\s+)?office|"
    r"(?:один|два|три|чотири|п'ять|кілька|\d+)\s+дн\w*\s+"
    r"(?:на\s+тиждень\s+)?(?:в|у)\s+офіс\w*"
    r")(?!\w)",
    re.IGNORECASE,
)
HEADLINE_PATTERN = re.compile(
    r"^(?P<title>.+)\s+в\s+(?P<company>[^,]+)(?:,\s*(?P<details>.+))?$",
    re.IGNORECASE,
)
VACANCY_ID_PATTERN = re.compile(r"/vacancies/(?P<identifier>\d+)(?:/|$)")
SALARY_PATTERN = re.compile(
    r"(?<!\w)(?P<prefix>від|до|from|up\s+to)?\s*"
    r"(?P<symbol>[$€])?\s*"
    r"(?P<first>\d[\d\s\u00a0.,]*)"
    r"(?:\s*(?:[-–—]|\bдо\b|\bto\b)\s*(?P<second_symbol>[$€])?\s*"
    r"(?P<second>\d[\d\s\u00a0.,]*))?\s*"
    r"(?P<code>usd|eur|uah|грн(?:\.|ивень|ивні|ивень)?|долар\w*|доллар\w*)?"
    r"(?!\w)",
    re.IGNORECASE,
)


class DouJobsSourceError(RuntimeError):
    pass


class DouJobsSource(BaseSource):
    name = "dou_jobs"
    display_name = "DOU Jobs"
    opportunity_kind = OpportunityKind.EMPLOYMENT
    allowed_listing_hosts = SOURCE_LISTING_HOSTS["dou_jobs"]
    deactivate_missing_listings = False

    def __init__(
        self,
        feed_url: str = DEFAULT_FEED_URL,
        request_timeout_seconds: float = 30.0,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
        additional_feed_urls: tuple[str, ...] = (),
        max_feeds_per_run: int | None = None,
        feed_delay_seconds: float = 0,
        local_city_aliases: tuple[str, ...] = (),
    ) -> None:
        self._feed_url = feed_url
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        self._client = client
        self._feed_urls = tuple(dict.fromkeys((feed_url, *additional_feed_urls)))
        if len(self._feed_urls) > 64:
            raise ValueError("At most 64 DOU feeds may be configured.")
        self._max_feeds_per_run = max_feeds_per_run
        self._feed_delay_seconds = feed_delay_seconds
        self._local_city_aliases = local_city_aliases

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._client is not None:
            async for listing in self._fetch_with_client(self._client):
                yield listing
            return

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(self._request_timeout_seconds),
            headers={"Accept": "application/rss+xml, application/xml", "User-Agent": USER_AGENT},
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        headline = _decode_html(_required_string(payload.get("title"), "title"))
        title, company, details = parse_dou_headline(headline)
        description_html = _required_string(payload.get("description"), "description")
        salary_min, salary_max, salary_currency = parse_salary(details)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=title,
            company=company,
            description=html_to_text(description_html),
            location_text=_location_text(details),
            work_mode=WorkMode(payload.get("work_mode", WorkMode.REMOTE.value)),
            employment_type=_employment_type(description_html),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period="month" if salary_currency is not None else None,
            published_at=_rss_datetime(payload.get("pubDate")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        urls = search_batch(
            self._feed_urls,
            self._max_feeds_per_run,
            getattr(self, "_discovery_run_index", 0),
            pinned=2,
        )
        budget = DownloadBudget(monotonic() + 600)
        listings: dict[str, RawListing] = {}
        succeeded = 0
        for index, url in enumerate(urls):
            if (
                budget.consumed_bytes >= budget.maximum_bytes
                or monotonic() + (self._feed_delay_seconds if index else 0) >= budget.deadline
            ):
                self.report_warning("DOU Jobs feed traversal stopped at its run budget.")
                break
            if index:
                await polite_delay(self._feed_delay_seconds)
            try:
                rows = await self._fetch_feed(client, url, budget)
            except DouJobsSourceError as error:
                self.report_warning(str(error))
                continue
            succeeded += 1
            for listing in rows:
                if listing.external_id in listings:
                    self.record_filtered()
                else:
                    listings[listing.external_id] = listing
        if not succeeded:
            raise DouJobsSourceError("Every configured DOU Jobs RSS feed failed.")
        # New identities take precedence over already known records when a run is capped.
        ordered = sorted(
            listings.values(), key=lambda row: self.cached_listing(row.external_id) is not None
        )
        for index, listing in enumerate(ordered):
            if index >= self._max_items:
                break
            yield listing
        if len(ordered) >= self._max_items:
            self.mark_limit_reached()

    async def _fetch_feed(
        self, client: httpx.AsyncClient, url: str, budget: DownloadBudget
    ) -> list[RawListing]:
        self.record_page()
        try:
            response = await bounded_get(
                client,
                url,
                timeout_seconds=self._request_timeout_seconds,
                maximum_bytes=MAX_FEED_BYTES,
                budget=budget,
            )
            response.raise_for_status()
            if len(response.content) > MAX_FEED_BYTES:
                raise DouJobsSourceError("DOU Jobs RSS response exceeded the size limit.")
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError, DefusedXmlException) as error:
            raise DouJobsSourceError(
                f"DOU Jobs RSS request failed ({type(error).__name__})."
            ) from error

        items = root.findall("./channel/item")
        self.record_candidates(len(items))
        seen: set[str] = set()
        listings: list[RawListing] = []
        malformed = 0
        for item in items:
            payload = {child.tag: child.text or "" for child in item}
            headline = _decode_html(_optional_string(payload.get("title")) or "")
            description = html_to_text(_optional_string(payload.get("description")) or "")
            mode = _work_mode(headline, description, self._local_city_aliases)
            if mode is None:
                self.record_filtered()
                continue
            source_url = _optional_string(payload.get("link")) or _optional_string(
                payload.get("guid")
            )
            if source_url is None:
                self.record_filtered()
                continue
            try:
                if not is_trusted_source_link(source_url, self.allowed_listing_hosts):
                    raise ValueError("Untrusted DOU vacancy URL.")
                external_id = _external_id(source_url)
                payload["work_mode"] = mode.value
                raw_listing = RawListing(
                    external_id=external_id,
                    source_url=source_url,
                    payload=payload,
                )
            except ValueError:
                malformed += 1
                self.record_filtered()
                continue
            if external_id in seen:
                self.record_filtered()
                continue
            seen.add(external_id)
            listings.append(raw_listing)
        if malformed:
            self.report_warning(f"DOU Jobs skipped {malformed} malformed RSS items.")
        return listings


def _work_mode(
    headline: str, description: str, local_city_aliases: tuple[str, ...]
) -> WorkMode | None:
    if _is_strict_remote(headline, description):
        return WorkMode.REMOTE
    _, _, details = parse_dou_headline(headline)
    location = normalize_text(details or "")
    if not any(
        re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", location)
        for alias in local_city_aliases
    ):
        return None
    if HYBRID_WORK_PATTERN.search(normalize_text(f"{headline} {description}")) or re.search(
        r"(?<!\w)hybrid(?!\w)", location
    ):
        return WorkMode.HYBRID
    return WorkMode.ONSITE


def parse_dou_headline(value: str) -> tuple[str, str | None, str | None]:
    decoded = _decode_html(value).strip()
    match = HEADLINE_PATTERN.fullmatch(decoded)
    if match is None:
        return decoded, None, None
    title = match.group("title").strip()
    company = match.group("company").strip()
    details = _optional_string(match.group("details"))
    if not title or not company:
        return decoded, None, details
    return title, company, details


def parse_salary(details: str | None) -> tuple[Decimal | None, Decimal | None, str | None]:
    if details is None:
        return None, None, None
    for segment in details.split(","):
        match = SALARY_PATTERN.search(segment)
        if match is None:
            continue
        currency = _salary_currency(match.group("symbol"), match.group("code"))
        if currency is None:
            continue
        first = _decimal_amount(match.group("first"))
        second = _decimal_amount(match.group("second"))
        if first is None:
            continue
        prefix = normalize_text(match.group("prefix"))
        if prefix in {"до", "up to"}:
            return None, first, currency
        if prefix in {"від", "from"}:
            return first, second, currency
        return first, second, currency
    return None, None, None


def _is_strict_remote(headline: str, description: str) -> bool:
    if REMOTE_HEADLINE_PATTERN.search(headline) is None:
        return False
    searchable_text = normalize_text(f"{headline} {description}")
    return HYBRID_WORK_PATTERN.search(searchable_text) is None


def _location_text(details: str | None) -> str:
    if details is None:
        return "Remote"
    locations = [
        segment.strip()
        for segment in details.split(",")
        if segment.strip() and parse_salary(segment) == (None, None, None)
    ]
    return ", ".join(locations) or "Remote"


def _employment_type(description_html: str) -> str | None:
    description = normalize_text(html_to_text(description_html))
    if re.search(r"(?<!\w)(?:full[- ]?time|повн\w*\s+зайнят\w*)(?!\w)", description):
        return "full_time"
    if re.search(r"(?<!\w)(?:part[- ]?time|частков\w*\s+зайнят\w*)(?!\w)", description):
        return "part_time"
    return None


def _external_id(source_url: str) -> str:
    path = urlsplit(source_url).path
    match = VACANCY_ID_PATTERN.search(path)
    if match is not None:
        return match.group("identifier")
    slug = path.rstrip("/").rsplit("/", maxsplit=1)[-1]
    if slug and len(slug) <= 255:
        return slug
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()


def _rss_datetime(value: Any) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _salary_currency(symbol: str | None, code: str | None) -> str | None:
    if symbol == "$":
        return "USD"
    if symbol == "€":
        return "EUR"
    normalized_code = normalize_text(code)
    if normalized_code in {"usd"} or normalized_code.startswith(("долар", "доллар")):
        return "USD"
    if normalized_code == "eur":
        return "EUR"
    if normalized_code == "uah" or normalized_code.startswith("грн"):
        return "UAH"
    return None


def _decimal_amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    normalized = re.sub(r"[\s\u00a0]", "", value).replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def _decode_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise DouJobsSourceError(f"DOU Jobs listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
