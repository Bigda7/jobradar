from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import parse_job_postings

DEFAULT_JOBS_URL = "https://djinni.co/jobs/l-nonhr/remote/"
USER_AGENT = "JobRadar/0.2 (personal job aggregator)"


class DjinniSourceError(RuntimeError):
    pass


class DjinniSource(BaseSource):
    name = "djinni"
    display_name = "Djinni"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        jobs_url: str = DEFAULT_JOBS_URL,
        remote_only: bool = True,
        request_timeout_seconds: float = 20.0,
        max_items: int = 50,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._jobs_url = jobs_url
        self._remote_only = remote_only
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items = max_items
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        html = await self._fetch_page()
        postings = parse_job_postings(html)
        if not postings:
            raise DjinniSourceError("Djinni page did not contain JobPosting JSON-LD data.")

        yielded = 0
        for posting in postings:
            if self._remote_only and _work_mode(posting) is not WorkMode.REMOTE:
                continue
            if yielded >= self._max_items:
                break
            yield _to_raw_listing(posting)
            yielded += 1

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        posting = raw_listing.payload
        salary_min, salary_max, salary_currency, salary_period = _salary(posting)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(posting.get("title"), "title"),
            company=_company(posting),
            description=_optional_string(posting.get("description")),
            location_text=_location(posting),
            work_mode=_work_mode(posting),
            employment_type=_employment_type(posting),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            published_at=_datetime(posting.get("datePosted")),
        )

    async def _fetch_page(self) -> str:
        if self._client is not None:
            return await self._request(self._client)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=timeout,
        ) as client:
            return await self._request(client)

    async def _request(self, client: httpx.AsyncClient) -> str:
        try:
            response = await client.get(self._jobs_url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise DjinniSourceError(f"Djinni request failed: {error}") from error
        return response.text


def _to_raw_listing(posting: dict[str, Any]) -> RawListing:
    external_id = posting.get("identifier")
    if isinstance(external_id, dict):
        external_id = external_id.get("value") or external_id.get("name")
    return RawListing(
        external_id=_required_string(external_id, "identifier"),
        source_url=_required_string(posting.get("url"), "url"),
        payload=posting,
    )


def _work_mode(posting: dict[str, Any]) -> WorkMode:
    location_type = posting.get("jobLocationType")
    values = location_type if isinstance(location_type, list) else [location_type]
    if any(str(value).casefold() == "telecommute" for value in values if value is not None):
        return WorkMode.REMOTE
    if posting.get("jobLocation"):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def _company(posting: dict[str, Any]) -> str | None:
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict):
        return _optional_string(organization.get("name"))
    return None


def _employment_type(posting: dict[str, Any]) -> str | None:
    value = posting.get("employmentType")
    if isinstance(value, list):
        return ",".join(str(item).casefold() for item in value)
    return str(value).casefold() if value is not None else None


def _salary(
    posting: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None, str | None, str | None]:
    salary = posting.get("estimatedSalary")
    if not isinstance(salary, dict):
        return None, None, None, None

    value = salary.get("value")
    amount = value if isinstance(value, dict) else salary
    minimum = _decimal(amount.get("minValue") or amount.get("value"))
    maximum = _decimal(amount.get("maxValue") or amount.get("value"))
    currency = _optional_string(salary.get("currency"))
    if currency is not None:
        currency = currency.upper()
    period = _optional_string(amount.get("unitText"))
    if period is not None:
        period = period.casefold()
    return minimum, maximum, currency, period


def _location(posting: dict[str, Any]) -> str | None:
    values = _location_values(posting.get("applicantLocationRequirements"))
    if not values:
        values = _location_values(posting.get("jobLocation"))
    if values:
        return ", ".join(dict.fromkeys(values))
    if _work_mode(posting) is WorkMode.REMOTE:
        return "Remote"
    return None


def _location_values(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    values: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if not isinstance(address, dict):
            continue
        for key in ("addressCountry", "addressRegion", "addressLocality"):
            location_value = address.get(key)
            if isinstance(location_value, str) and location_value.strip():
                values.append(location_value.strip())
    return values


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise DjinniSourceError(f"Djinni JobPosting is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
