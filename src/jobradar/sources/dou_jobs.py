import hashlib
import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.domain.normalization import normalize_text
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_FEED_URL = "https://jobs.dou.ua/vacancies/feeds/?remote"
USER_AGENT = "JobRadar/1.4 (personal job aggregator)"

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
    deactivate_missing_listings = False

    def __init__(
        self,
        feed_url: str = DEFAULT_FEED_URL,
        request_timeout_seconds: float = 30.0,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._feed_url = feed_url
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        self._client = client

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
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(description_html),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period="month" if salary_currency is not None else None,
            published_at=_rss_datetime(payload.get("pubDate")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        try:
            response = await client.get(self._feed_url)
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
        except (httpx.HTTPError, ElementTree.ParseError) as error:
            raise DouJobsSourceError(f"DOU Jobs RSS request failed: {error}") from error

        seen: set[str] = set()
        yielded = 0
        for item in root.findall("./channel/item"):
            payload = {child.tag: child.text or "" for child in item}
            headline = _decode_html(_optional_string(payload.get("title")) or "")
            description = html_to_text(_optional_string(payload.get("description")) or "")
            if not _is_strict_remote(headline, description):
                continue
            source_url = _optional_string(payload.get("link")) or _optional_string(
                payload.get("guid")
            )
            if source_url is None:
                continue
            external_id = _external_id(source_url)
            if external_id in seen:
                continue
            seen.add(external_id)
            yield RawListing(
                external_id=external_id,
                source_url=source_url,
                payload=payload,
            )
            yielded += 1
            if yielded >= self._max_items:
                return


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
