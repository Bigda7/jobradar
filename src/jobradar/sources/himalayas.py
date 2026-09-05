from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_URL = "https://himalayas.app/jobs/api"
USER_AGENT = "JobRadar/1.4 (personal job aggregator)"
MAX_API_PAGE_SIZE = 20


class HimalayasSourceError(RuntimeError):
    pass


class HimalayasSource(BaseSource):
    name = "himalayas"
    display_name = "Himalayas"
    opportunity_kind = OpportunityKind.EMPLOYMENT
    deactivate_missing_listings = False

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        request_timeout_seconds: float = 30.0,
        page_size: int = MAX_API_PAGE_SIZE,
        max_pages: int = 10,
        max_items: int = 200,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._request_timeout_seconds = request_timeout_seconds
        self._page_size = min(page_size, MAX_API_PAGE_SIZE)
        self._max_pages = max_pages
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
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        salary_min = _decimal(payload.get("minSalary"))
        salary_max = _decimal(payload.get("maxSalary"))
        salary_currency = _salary_currency(
            payload.get("currency"), salary_min=salary_min, salary_max=salary_max
        )
        if salary_currency is None:
            salary_min = None
            salary_max = None

        description_html = _optional_string(payload.get("description"))
        description = html_to_text(description_html) if description_html else None
        if not description:
            description = _optional_string(payload.get("excerpt"))

        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_required_string(payload.get("companyName"), "companyName"),
            description=description,
            location_text=_location_text(payload.get("locationRestrictions")),
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(payload.get("employmentType")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=(
                _salary_period(payload.get("salaryPeriod")) if salary_currency is not None else None
            ),
            published_at=_datetime(payload.get("pubDate")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        cursor: str | None = None
        seen: set[str] = set()
        yielded = 0

        for _ in range(self._max_pages):
            remaining = self._max_items - yielded
            if remaining <= 0:
                break
            params: dict[str, str | int] = {"limit": min(self._page_size, remaining)}
            if cursor is not None:
                params["cursor"] = cursor

            self.record_page()
            payload = await self._request_page(client, params)
            jobs = payload.get("jobs")
            if not isinstance(jobs, list):
                raise HimalayasSourceError("Himalayas response is missing the jobs list.")
            self.record_candidates(len(jobs))

            for item in jobs:
                if not isinstance(item, dict):
                    self.record_filtered()
                    continue
                external_id = _optional_string(item.get("guid"))
                source_url = _optional_string(item.get("applicationLink"))
                if external_id is None or source_url is None or external_id in seen:
                    self.record_filtered()
                    continue
                seen.add(external_id)
                yielded += 1
                yield RawListing(
                    external_id=external_id,
                    source_url=source_url,
                    payload=item,
                )
                if yielded >= self._max_items:
                    self.mark_limit_reached()
                    return

            next_cursor = _optional_string(payload.get("nextCursor"))
            if next_cursor is None or next_cursor == cursor or not jobs:
                break
            cursor = next_cursor

    async def _request_page(
        self,
        client: httpx.AsyncClient,
        params: Mapping[str, str | int],
    ) -> Mapping[str, Any]:
        try:
            response = await client.get(self._api_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise HimalayasSourceError(f"Himalayas request failed: {error}") from error
        if not isinstance(payload, Mapping):
            raise HimalayasSourceError("Himalayas returned a non-object JSON response.")
        return payload


def _location_text(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "Worldwide"

    countries: list[tuple[str | None, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            name = _optional_string(item.get("name"))
            alpha2 = _optional_string(item.get("alpha2"))
            if name:
                countries.append((alpha2.upper() if alpha2 else None, name))
        elif isinstance(item, str) and item.strip():
            countries.append((None, item.strip()))

    if len(countries) == 1 and (
        countries[0][0] == "US" or countries[0][1].casefold() == "united states"
    ):
        return "United States Only"
    return ", ".join(name for _, name in countries) or "Remote"


def _employment_type(value: Any) -> str | None:
    normalized = (_optional_string(value) or "").casefold().replace("-", " ")
    return {
        "full time": "full_time",
        "part time": "part_time",
        "contractor": "contractor",
        "temporary": "temporary",
        "intern": "internship",
        "volunteer": "volunteer",
        "other": "other",
    }.get(normalized)


def _salary_period(value: Any) -> str:
    normalized = (_optional_string(value) or "annual").casefold()
    return {
        "hourly": "hour",
        "weekly": "week",
        "fortnightly": "fortnight",
        "monthly": "month",
        "annual": "year",
    }.get(normalized, "year")


def _salary_currency(
    value: Any,
    *,
    salary_min: Decimal | None,
    salary_max: Decimal | None,
) -> str | None:
    if salary_min is None and salary_max is None:
        return None
    currency = (_optional_string(value) or "").upper()
    return currency if len(currency) == 3 and currency.isalpha() else None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
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
    parsed = _optional_string(value)
    if parsed is None:
        raise HimalayasSourceError(f"Himalayas job is missing {field_name}.")
    return parsed


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed or None
