import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_BASE_URL = "https://api.startup.jobs"
USER_AGENT = "JobRadar/1.0 (personal job aggregator)"
_SALARY_PATTERN = re.compile(
    r"(?P<currency>USD|EUR|GBP|CZK|UAH|\$|€|£)\s*"
    r"(?P<minimum>[\d,.]+)\s*(?:-|–|—|to)\s*"
    r"(?P<maximum>[\d,.]+)"
    r"(?:\s*(?:per|/)\s*(?P<period>hour|day|week|month|year))?",
    re.IGNORECASE,
)
_CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP"}


class StartupJobsSourceError(RuntimeError):
    pass


class StartupJobsSource(BaseSource):
    name = "startup_jobs"
    display_name = "Startup.jobs"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        api_key: str,
        api_base_url: str = DEFAULT_API_BASE_URL,
        role: str = "engineering",
        request_timeout_seconds: float = 30.0,
        page_size: int = 50,
        max_pages: int = 2,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("A Startup.jobs API key is required.")
        self._api_key = api_key.strip()
        self._api_base_url = api_base_url.rstrip("/")
        self._role = role.strip()
        self._request_timeout_seconds = request_timeout_seconds
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_items = max_items
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._client is not None:
            async for listing in self._fetch_with_client(self._client):
                yield listing
            return

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": USER_AGENT,
            },
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        if _optional_string(payload.get("workplace_type")) != "remote":
            raise StartupJobsSourceError("Startup.jobs listing is not fully remote.")
        salary_min, salary_max, currency, period = _salary(payload)
        description_html = _required_string(payload.get("description_html"), "description_html")
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_company_name(payload.get("company")),
            description=html_to_text(description_html),
            location_text=_location_text(payload.get("location")),
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(payload.get("employment_type")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period=period,
            published_at=_datetime(payload.get("published_at")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        cursor: int | None = None
        seen: set[str] = set()
        yielded = 0
        for _ in range(self._max_pages):
            params: dict[str, str | int] = {
                "workplace_type": "remote",
                "role": self._role,
                "limit": self._page_size,
            }
            if cursor is not None:
                params["starting_after"] = cursor
            response = await self._request_page(client, params)
            items = response.get("data")
            if not isinstance(items, list):
                raise StartupJobsSourceError("Startup.jobs response is missing the data list.")
            for item in items:
                if not isinstance(item, dict):
                    continue
                external_id = _optional_string(item.get("id"))
                source_url = _optional_string(item.get("url"))
                if (
                    external_id is None
                    or source_url is None
                    or external_id in seen
                    or _optional_string(item.get("workplace_type")) != "remote"
                ):
                    continue
                seen.add(external_id)
                yield RawListing(
                    external_id=external_id,
                    source_url=source_url,
                    payload=item,
                )
                yielded += 1
                if yielded >= self._max_items:
                    return
            has_more = response.get("has_more") is True
            next_cursor = response.get("next_cursor")
            if not has_more or not isinstance(next_cursor, int):
                return
            cursor = next_cursor

    async def _request_page(
        self,
        client: httpx.AsyncClient,
        params: dict[str, str | int],
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{self._api_base_url}/v1/jobs",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise StartupJobsSourceError(f"Startup.jobs request failed: {error}") from error
        if not isinstance(value, dict):
            raise StartupJobsSourceError("Startup.jobs returned a non-object JSON response.")
        return value


def _salary(
    payload: Mapping[str, Any],
) -> tuple[Decimal | None, Decimal | None, str | None, str | None]:
    structured = payload.get("salary_data")
    if isinstance(structured, Mapping):
        minimum = _decimal(structured.get("min"))
        maximum = _decimal(structured.get("max"))
        currency = _optional_string(structured.get("currency"))
        period = _optional_string(structured.get("interval"))
        if currency and (minimum is not None or maximum is not None):
            return minimum, maximum, currency.upper(), period.casefold() if period else None

    salary_text = _optional_string(payload.get("salary"))
    if salary_text is None:
        return None, None, None, None
    match = _SALARY_PATTERN.search(salary_text)
    if match is None:
        return None, None, None, None
    raw_currency = match.group("currency").upper()
    currency = _CURRENCY_CODES.get(raw_currency, raw_currency)
    return (
        _decimal(match.group("minimum"), strip_grouping=True),
        _decimal(match.group("maximum"), strip_grouping=True),
        currency,
        match.group("period").casefold() if match.group("period") else None,
    )


def _decimal(value: Any, *, strip_grouping: bool = False) -> Decimal | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if strip_grouping:
        normalized = normalized.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _company_name(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _optional_string(value.get("name"))


def _location_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "Remote"
    parts: list[str] = []
    for key in ("city", "state", "country"):
        part = _optional_string(value.get(key))
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts) if parts else "Remote"


def _employment_type(value: Any) -> str | None:
    text = _optional_string(value)
    return text.casefold().replace("-", "_") if text else None


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


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise StartupJobsSourceError(f"Startup.jobs listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
