from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_URL = "https://jobicy.com/api/v2/remote-jobs"
USER_AGENT = "JobRadar/1.1 (personal job aggregator)"


class JobicySourceError(RuntimeError):
    pass


class JobicySource(BaseSource):
    name = "jobicy"
    display_name = "Jobicy"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        industry: str = "engineering",
        request_timeout_seconds: float = 30.0,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._industry = industry.strip()
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
        description_html = _required_string(payload.get("jobDescription"), "jobDescription")
        salary_min = _decimal(payload.get("salaryMin"))
        salary_max = _decimal(payload.get("salaryMax"))
        salary_currency = _optional_string(payload.get("salaryCurrency"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("jobTitle"), "jobTitle"),
            company=_required_string(payload.get("companyName"), "companyName"),
            description=html_to_text(description_html),
            location_text=_optional_string(payload.get("jobGeo")) or "Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(payload.get("jobType")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency.upper() if salary_currency else None,
            salary_period=_salary_period(payload.get("salaryPeriod")),
            published_at=_datetime(payload.get("pubDate")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        params: dict[str, str | int] = {"count": self._max_items}
        if self._industry:
            params["industry"] = self._industry
        try:
            response = await client.get(self._api_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise JobicySourceError(f"Jobicy request failed: {error}") from error
        if not isinstance(payload, Mapping):
            raise JobicySourceError("Jobicy returned a non-object JSON response.")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise JobicySourceError("Jobicy response is missing the jobs list.")

        seen: set[str] = set()
        for item in jobs:
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


def _employment_type(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    types = [
        text.casefold().replace("-", "_").replace(" ", "_")
        for item in value
        if (text := _optional_string(item)) is not None
    ]
    return ",".join(types) or None


def _salary_period(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    normalized = text.casefold()
    periods = {
        "hourly": "hour",
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
        "yearly": "year",
        "annually": "year",
    }
    return periods.get(normalized, normalized)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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
        raise JobicySourceError(f"Jobicy listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
