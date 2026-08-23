import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_URL = "https://remotive.com/api/remote-jobs"
USER_AGENT = "JobRadar/1.2 (personal job aggregator)"
NUMBER_PATTERN = re.compile(r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*([kK])?")


class RemotiveSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    minimum: Decimal
    maximum: Decimal
    currency: str
    period: str


class RemotiveSource(BaseSource):
    name = "remotive"
    display_name = "Remotive"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        category: str = "software-dev",
        request_timeout_seconds: float = 30.0,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._category = category.strip()
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
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        description_html = _required_string(payload.get("description"), "description")
        salary = parse_salary(_optional_string(payload.get("salary")))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_required_string(payload.get("company_name"), "company_name"),
            description=html_to_text(description_html),
            location_text=(
                _optional_string(payload.get("candidate_required_location")) or "Remote"
            ),
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(payload.get("job_type")),
            salary_min=salary.minimum if salary else None,
            salary_max=salary.maximum if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            published_at=_datetime(payload.get("publication_date")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        params: dict[str, str | int] = {"limit": self._max_items}
        if self._category:
            params["category"] = self._category
        try:
            response = await client.get(self._api_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RemotiveSourceError(f"Remotive request failed: {error}") from error
        if not isinstance(payload, Mapping):
            raise RemotiveSourceError("Remotive returned a non-object JSON response.")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise RemotiveSourceError("Remotive response is missing the jobs list.")

        seen: set[str] = set()
        for item in jobs[: self._max_items]:
            if not isinstance(item, dict):
                continue
            external_id = _optional_string(item.get("id"))
            source_url = _optional_string(item.get("url"))
            if external_id is None or source_url is None or external_id in seen:
                continue
            seen.add(external_id)
            yield RawListing(
                external_id=external_id,
                source_url=source_url,
                payload=item,
            )


def parse_salary(value: str | None) -> ParsedSalary | None:
    if value is None:
        return None
    currency = _currency(value)
    if currency is None:
        return None
    amounts = [_amount(match) for match in NUMBER_PATTERN.finditer(value)]
    parsed_amounts = [amount for amount in amounts if amount is not None]
    if not parsed_amounts:
        return None
    minimum = parsed_amounts[0]
    maximum = parsed_amounts[1] if len(parsed_amounts) > 1 else minimum
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return ParsedSalary(
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        period=_period(value),
    )


def _amount(match: re.Match[str]) -> Decimal | None:
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if match.group(2):
        amount *= Decimal("1000")
    return amount


def _currency(value: str) -> str | None:
    normalized = value.upper()
    currencies = {
        "USD": ("USD", "$"),
        "EUR": ("EUR", "€"),
        "GBP": ("GBP", "£"),
        "CZK": ("CZK", "KČ"),
        "UAH": ("UAH", "₴"),
    }
    for code, markers in currencies.items():
        if any(marker in normalized for marker in markers):
            return code
    return None


def _period(value: str) -> str:
    normalized = value.casefold()
    periods = {
        "hour": ("/hour", "per hour", "hourly", "/hr", " hod"),
        "day": ("/day", "per day", "daily"),
        "week": ("/week", "per week", "weekly"),
        "month": ("/month", "per month", "monthly", "/mo"),
        "year": ("/year", "per year", "yearly", "annual", "/yr"),
    }
    for period, markers in periods.items():
        if any(marker in normalized for marker in markers):
            return period
    return "year"


def _employment_type(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    return text.casefold().replace("-", "_").replace(" ", "_")


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
        raise RemotiveSourceError(f"Remotive listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
