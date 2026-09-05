import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
from jobradar.sources.structured_data import html_to_text

DEFAULT_READER_BASE_URL = "https://r.jina.ai/http://robota.ua"
DEFAULT_API_READER_BASE_URL = "https://r.jina.ai/http://api.robota.ua"
DEFAULT_SEARCH_URLS = (
    "https://robota.ua/zapros/developer-remote/ukraine",
    "https://robota.ua/zapros/junior-developer-remote/ukraine",
    "https://robota.ua/zapros/frontend-developer-remote/ukraine",
    "https://robota.ua/zapros/backend-developer-remote/ukraine",
    "https://robota.ua/zapros/full-stack-developer-remote/ukraine",
    "https://robota.ua/zapros/python-remote/ukraine",
    "https://robota.ua/zapros/react-remote/ukraine",
)
USER_AGENT = "JobRadar/0.5 (personal job aggregator)"
VACANCY_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?robota\.ua/company\d+/vacancy(?P<id>\d+)(?:\?[^)\s]*)?",
    flags=re.IGNORECASE,
)
COMPANY_LINK_PATTERN = re.compile(
    r"\[([^\]]+)]\(https?://(?:www\.)?robota\.ua/company\d+/?(?:\?[^)]*)?\)",
    flags=re.IGNORECASE,
)
HEADING_PATTERN = re.compile(r"^#{1,2}\s+(.+?)\s*$")
DATE_PATTERN = re.compile(
    r"^(\d{1,2})\s+"
    r"(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|"
    r"жовтня|листопада|грудня)\s+(\d{4})$",
    flags=re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(r"\d+(?:[\s\u00a0\u2009\u202f.,]\d+)*")
UKRAINIAN_MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}
REMOTE_LABEL = "Віддалена робота"
FULL_TIME_LABEL = "Повна зайнятість"
PART_TIME_LABELS = {"Неповна зайнятість", "Часткова зайнятість"}
RESUME_PROMPT = "Створіть резюме, щоб оцінити свої шанси на вакансію"
SEARCH_CONCURRENCY = 3
API_EMPLOYMENT_TYPES = {1: "full_time", 2: "part_time"}


class RobotaUaSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RobotaUaCard:
    external_id: str
    url: str
    is_remote: bool


@dataclass(frozen=True, slots=True)
class RobotaUaDetail:
    title: str
    company: str | None
    description: str
    salary_text: str | None
    location_text: str | None
    employment_type: str | None
    published_at: datetime | None
    is_remote: bool


class RobotaUaSource(BaseSource):
    name = "robota_ua"
    display_name = "Robota.ua"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        search_urls: tuple[str, ...] = DEFAULT_SEARCH_URLS,
        reader_base_url: str = DEFAULT_READER_BASE_URL,
        api_reader_base_url: str = DEFAULT_API_READER_BASE_URL,
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
        self._api_reader_base_url = api_reader_base_url.rstrip("/")
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
        card_batches: list[list[RobotaUaCard]] = []
        semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
        search_results = await asyncio.gather(
            *(self._collect_search(search_url, semaphore) for search_url in self._search_urls)
        )
        successful_search_pages = sum(result[0] for result in search_results)
        for _, batches in search_results:
            card_batches.extend(batches)

        if not card_batches:
            if successful_search_pages:
                raise RobotaUaSourceError(
                    "Configured Robota.ua searches returned no vacancy cards."
                )
            raise RobotaUaSourceError("Every configured Robota.ua search page failed.")

        for cards in card_batches:
            for card in cards:
                if card.external_id in seen:
                    continue
                seen.add(card.external_id)
                if self._remote_only and not card.is_remote:
                    self.record_filtered()
                    continue

                discovery_payload = {"is_remote": card.is_remote}
                fingerprint = discovery_fingerprint(discovery_payload)
                cached = self.cached_listing(card.external_id)
                now = datetime.now(UTC)
                if cached is not None and can_reuse_detail(
                    cached,
                    fingerprint=fingerprint,
                    cached_fingerprint=discovery_fingerprint(
                        {"is_remote": bool(cached.payload.get("is_remote"))}
                    ),
                    required_fields=("title", "description"),
                    ttl_seconds=self._detail_cache_ttl_seconds,
                    now=now,
                ):
                    payload = dict(cached.payload)
                    detail_fetched_at = cached.detail_fetched_at
                else:
                    await polite_delay(self._detail_request_delay_seconds)
                    try:
                        detail = await self._fetch_detail(card)
                    except RobotaUaSourceError as error:
                        self.report_warning(str(error))
                        self.record_detail_failure()
                        continue
                    if detail is None:
                        self.record_detail_failure()
                        continue
                    if self._remote_only and not detail.is_remote:
                        self.record_filtered()
                        continue
                    payload = _detail_payload(detail)
                    detail_fetched_at = datetime.now(UTC)

                yield RawListing(
                    external_id=card.external_id,
                    source_url=card.url,
                    payload=payload,
                    detail_fetched_at=detail_fetched_at,
                )
                yielded += 1
                if yielded >= self._max_items:
                    self.mark_limit_reached()
                    return

    async def _collect_search(
        self, search_url: str, semaphore: asyncio.Semaphore
    ) -> tuple[int, list[list[RobotaUaCard]]]:
        successful_pages = 0
        batches: list[list[RobotaUaCard]] = []
        query_ids: set[str] = set()
        for page_number in range(1, self._max_pages_per_search + 1):
            page_url = _page_url(search_url, page_number)
            self.record_page()
            try:
                async with semaphore:
                    cards = await self._fetch_search_cards(page_url)
            except RobotaUaSourceError as error:
                self.report_warning(str(error))
                break
            successful_pages += 1
            self.record_candidates(len(cards))
            if not cards:
                break
            new_cards = [card for card in cards if card.external_id not in query_ids]
            if not new_cards:
                break
            query_ids.update(card.external_id for card in new_cards)
            batches.append(new_cards)
        return successful_pages, batches

    async def _fetch_search_cards(self, search_url: str) -> list[RobotaUaCard]:
        cards: list[RobotaUaCard] = []
        for _ in range(self._retry_attempts):
            markdown = await self._fetch_page(search_url)
            cards = parse_robota_ua_cards(markdown)
            if cards:
                break
        return cards

    async def _fetch_detail(self, card: RobotaUaCard) -> RobotaUaDetail | None:
        try:
            api_response = await self._fetch_api_detail(card.external_id)
            return parse_robota_ua_api_detail(
                api_response,
                expected_external_id=card.external_id,
                is_remote=card.is_remote,
            )
        except RobotaUaSourceError as error:
            self.report_warning(f"{error} Falling back to the vacancy page.")

        try:
            markdown = await self._fetch_page(card.url)
        except RobotaUaSourceError as error:
            if "404" in str(error) or "410" in str(error):
                return None
            raise
        return parse_robota_ua_detail(markdown)

    async def _fetch_api_detail(self, external_id: str) -> str:
        request_url = f"{self._api_reader_base_url}/vacancy?id={external_id}"
        if self._client is not None:
            return await self._request(self._client, request_url)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
            timeout=timeout,
        ) as client:
            return await self._request(client, request_url)

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        salary_min, salary_max, salary_currency = parse_robota_ua_salary(payload.get("salary_text"))
        is_remote = bool(payload.get("is_remote"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_optional_string(payload.get("company")),
            description=_required_string(payload.get("description"), "description"),
            location_text=_optional_string(payload.get("location_text"))
            or ("Remote" if is_remote else None),
            work_mode=WorkMode.REMOTE if is_remote else WorkMode.UNKNOWN,
            employment_type=_optional_string(payload.get("employment_type")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period="month" if salary_currency is not None else None,
            published_at=_datetime(payload.get("published_at")),
        )

    async def _fetch_page(self, source_url: str) -> str:
        request_url = _reader_url(self._reader_base_url, source_url)
        if self._client is not None:
            return await self._request(self._client, request_url)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
            timeout=timeout,
        ) as client:
            return await self._request(client, request_url)

    async def _request(self, client: httpx.AsyncClient, request_url: str) -> str:
        try:
            response = await get_with_backoff(
                client,
                request_url,
                attempts=self._retry_attempts,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise RobotaUaSourceError(
                f"Robota.ua reader request failed ({type(error).__name__})."
            ) from error
        return response.text


def parse_robota_ua_cards(markdown: str) -> list[RobotaUaCard]:
    cards: list[RobotaUaCard] = []
    seen: set[str] = set()
    for match in VACANCY_URL_PATTERN.finditer(markdown):
        external_id = match.group("id")
        if external_id in seen:
            continue
        card_text = _card_context(markdown, match.start(), match.end())
        cards.append(
            RobotaUaCard(
                external_id=external_id,
                url=_canonical_vacancy_url(match.group(0)),
                is_remote=(
                    REMOTE_LABEL.casefold() in card_text.casefold()
                    or "(віддалено)" in card_text.casefold()
                ),
            )
        )
        seen.add(external_id)
    return cards


def parse_robota_ua_detail(markdown: str) -> RobotaUaDetail | None:
    content = markdown.partition("Markdown Content:")[2] or markdown
    lines = [line.strip() for line in content.splitlines()]
    title_index = _first_title_index(lines)
    if title_index is None:
        return None
    title_match = HEADING_PATTERN.match(lines[title_index])
    if title_match is None:
        return None
    title = _strip_markdown(title_match.group(1))
    published_at, date_index = _published_date(lines, title_index)
    metadata_end = min(len(lines), title_index + 35)
    company = _company_name(lines, title_index, metadata_end)
    salary_text = _salary_text(lines, title_index, metadata_end)
    location_text = _location_text(lines, date_index, metadata_end, salary_text)
    employment_type = _employment_type(lines, title_index, metadata_end)
    description_start = _description_start(lines, title_index, metadata_end)
    description_end = _description_end(lines, description_start)
    description = _description_text(lines[description_start:description_end])
    if not title or not description:
        return None
    return RobotaUaDetail(
        title=title,
        company=company,
        description=description,
        salary_text=salary_text,
        location_text=location_text,
        employment_type=employment_type,
        published_at=published_at,
        is_remote=any(
            _clean_text(line).casefold() == REMOTE_LABEL.casefold()
            for line in lines[title_index:description_start]
        ),
    )


def parse_robota_ua_api_detail(
    response_text: str,
    *,
    expected_external_id: str,
    is_remote: bool,
) -> RobotaUaDetail | None:
    content = (response_text.partition("Markdown Content:")[2] or response_text).strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise RobotaUaSourceError("Robota.ua API reader returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise RobotaUaSourceError("Robota.ua API reader returned an invalid payload.")
    if str(payload.get("id")) != expected_external_id:
        raise RobotaUaSourceError("Robota.ua API reader returned an unexpected vacancy.")
    if payload.get("isActive") is False:
        return None

    description_html = _required_string(payload.get("description"), "description")
    description = _clean_text(html_to_text(description_html))
    if not description:
        raise RobotaUaSourceError("Robota.ua vacancy is missing description.")

    return RobotaUaDetail(
        title=_required_string(payload.get("name"), "title"),
        company=_optional_string(payload.get("companyName")),
        description=description,
        salary_text=_api_salary_text(payload),
        location_text=_optional_string(payload.get("cityName")),
        employment_type=_api_employment_type(payload.get("scheduleId")),
        published_at=_datetime(payload.get("date")),
        is_remote=is_remote,
    )


def parse_robota_ua_salary(
    value: Any,
) -> tuple[Decimal | None, Decimal | None, str | None]:
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


def _detail_payload(detail: RobotaUaDetail) -> dict[str, Any]:
    return {
        "title": detail.title,
        "company": detail.company,
        "description": detail.description,
        "salary_text": detail.salary_text,
        "location_text": detail.location_text,
        "employment_type": detail.employment_type,
        "published_at": detail.published_at.isoformat() if detail.published_at else None,
        "is_remote": detail.is_remote,
    }


def _api_salary_text(payload: dict[str, Any]) -> str | None:
    comment = _optional_string(payload.get("salaryComment"))
    if comment is not None and _salary_currency(comment) is not None:
        return comment

    minimum = _positive_decimal(payload.get("salaryFrom"))
    maximum = _positive_decimal(payload.get("salaryTo"))
    exact = _positive_decimal(payload.get("salary"))
    if minimum is not None and maximum is not None and minimum != maximum:
        return f"{_format_decimal(minimum)} — {_format_decimal(maximum)} ₴"
    amount = minimum or maximum or exact
    if amount is None:
        return None
    return f"{_format_decimal(amount)} ₴"


def _api_employment_type(value: Any) -> str | None:
    try:
        schedule_id = int(value)
    except (TypeError, ValueError):
        return None
    return API_EMPLOYMENT_TYPES.get(schedule_id)


def _first_title_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[:40]):
        match = HEADING_PATTERN.match(line)
        if match is not None and not match.group(1).startswith("["):
            return index
    return None


def _published_date(lines: list[str], title_index: int) -> tuple[datetime | None, int | None]:
    for index in range(title_index + 1, min(len(lines), title_index + 20)):
        match = DATE_PATTERN.match(_clean_text(lines[index]))
        if match is None:
            continue
        return (
            datetime(
                int(match.group(3)),
                UKRAINIAN_MONTHS[match.group(2).casefold()],
                int(match.group(1)),
                tzinfo=UTC,
            ),
            index,
        )
    return None, None


def _company_name(lines: list[str], start: int, end: int) -> str | None:
    for line in lines[start:end]:
        match = COMPANY_LINK_PATTERN.search(line)
        if match is not None:
            return _strip_markdown(match.group(1)) or None
    return None


def _salary_text(lines: list[str], start: int, end: int) -> str | None:
    for line in lines[start:end]:
        value = _clean_text(line)
        if _salary_currency(value) is not None and NUMBER_PATTERN.search(value):
            return value
    return None


def _location_text(
    lines: list[str], date_index: int | None, end: int, salary_text: str | None
) -> str | None:
    if date_index is None:
        return None
    for line in lines[date_index + 1 : end]:
        value = _clean_text(line)
        if not value or value == salary_text:
            continue
        if value in {REMOTE_LABEL, FULL_TIME_LABEL, *PART_TIME_LABELS}:
            continue
        if value.startswith("[") or value.startswith("Створіть резюме"):
            continue
        if value.startswith("Система порівняє") or value == "Створити резюме":
            continue
        return _strip_markdown(value) or None
    return None


def _employment_type(lines: list[str], start: int, end: int) -> str | None:
    values = {_clean_text(line) for line in lines[start:end]}
    types: list[str] = []
    if FULL_TIME_LABEL in values:
        types.append("full_time")
    if values.intersection(PART_TIME_LABELS):
        types.append("part_time")
    return ",".join(types) or None


def _description_start(lines: list[str], title_index: int, metadata_end: int) -> int:
    for index in range(title_index + 1, metadata_end):
        if _clean_text(lines[index]) != RESUME_PROMPT:
            continue
        index += 1
        while index < len(lines):
            value = _clean_text(lines[index])
            if not value or value.startswith("Система порівняє") or value == "Створити резюме":
                index += 1
                continue
            return index

    last_metadata_index = title_index
    known_values = {REMOTE_LABEL, FULL_TIME_LABEL, *PART_TIME_LABELS}
    for index in range(title_index + 1, metadata_end):
        if _clean_text(lines[index]) in known_values:
            last_metadata_index = index
    index = last_metadata_index + 1
    while index < len(lines) and not _clean_text(lines[index]):
        index += 1
    return index


def _description_end(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        value = _clean_text(lines[index])
        if value == "Показати контакти":
            end = index
            while end > start and not _clean_text(lines[end - 1]):
                end -= 1
            if end > start and _looks_like_contact_name(_clean_text(lines[end - 1])):
                end -= 1
            return end
        if value.startswith("[Поділитися на Facebook]") or value.startswith("## ["):
            return index
        if value in {"Відгукнутись", "Гарячі вакансії"}:
            return index
    return len(lines)


def _card_context(markdown: str, url_start: int, url_end: int) -> str:
    context_start = markdown.rfind("\n", 0, url_start) + 1
    current_line = markdown[context_start:url_start].lstrip()
    if re.match(r"(?:#{1,3}\s+)?\[(?!Image\b)", current_line) is None:
        cursor = context_start
        while cursor > 0:
            previous_line_end = cursor - 1
            previous_line_start = markdown.rfind("\n", 0, previous_line_end) + 1
            previous_line = markdown[previous_line_start:previous_line_end].lstrip()
            if re.match(r"(?:#{1,3}\s+)?\[(?!Image\b)", previous_line):
                context_start = previous_line_start
                break
            cursor = previous_line_start
    context_end = markdown.find("\n", url_end)
    if context_end < 0:
        context_end = len(markdown)
    return markdown[context_start:context_end]


def _looks_like_contact_name(value: str) -> bool:
    if not value or len(value) > 80 or len(value.split()) > 5:
        return False
    if value.startswith(("#", "*", "-", "[")):
        return False
    return not value.endswith((".", "!", "?", ":", ";"))


def _description_text(lines: list[str]) -> str | None:
    parts: list[str] = []
    for line in lines:
        value = _strip_markdown(line)
        if value:
            parts.append(value)
    result = "\n".join(parts).strip()
    return result or None


def _strip_markdown(value: str) -> str:
    result = re.sub(r"!\[[^\]]*]\([^)]*\)", "", value)
    result = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", result)
    result = re.sub(r"^#{1,6}\s+", "", result)
    result = re.sub(r"^[-*+]\s+", "", result)
    result = result.replace("**", "").replace("__", "").replace("`", "")
    return _clean_text(result)


def _salary_currency(value: str) -> str | None:
    normalized = value.casefold()
    if "uah" in normalized or "грн" in normalized or "₴" in value:
        return "UAH"
    if "usd" in normalized or "$" in value:
        return "USD"
    if "eur" in normalized or "€" in value:
        return "EUR"
    return None


def _decimal(value: str) -> Decimal | None:
    normalized = re.sub(r"[\s\u00a0\u2009\u202f]", "", value).replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError):
        return None


def _positive_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _reader_url(reader_base_url: str, source_url: str) -> str:
    parsed = urlsplit(source_url)
    if parsed.hostname not in {"robota.ua", "www.robota.ua"}:
        raise RobotaUaSourceError(f"Unsupported Robota.ua URL: {source_url}")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"{reader_base_url}{path}"


def _page_url(search_url: str, page_number: int) -> str:
    if page_number <= 1:
        return search_url
    parsed = urlsplit(search_url)
    path = re.sub(r"/params;page=\d+/?$", "", parsed.path.rstrip("/"))
    path = f"{path}/params;page={page_number}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _canonical_vacancy_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit(("https", "robota.ua", parsed.path.rstrip("/"), "", ""))


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _optional_string(value)
        if text is None:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise RobotaUaSourceError(f"Robota.ua vacancy is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = _clean_text(str(value))
    return result or None


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())
