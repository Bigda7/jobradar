import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_URL = "https://www.themuse.com/api/public/jobs"
USER_AGENT = "JobRadar/1.5 (personal job aggregator)"
API_PAGE_SIZE = 20
REMOTE_LOCATION_NAMES = frozenset({"flexible / remote", "remote / flexible", "remote"})
SALARY_PATTERN = re.compile(
    r"(?:(?P<label>base\s+(?:pay|salary)|salary|compensation)\s*(?:range)?\s*:?\s*)?"
    r"(?P<currency>USD|EUR|GBP|CAD|AUD|\$|€|£)\s*"
    r"(?P<minimum>\d[\d,]*(?:\.\d+)?)\s*(?P<minimum_k>[kK])?"
    r"(?:\s*(?:-|–|—|to)\s*"
    r"(?:(?P<maximum_currency>USD|EUR|GBP|CAD|AUD|\$|€|£)\s*)?"
    r"(?P<maximum>\d[\d,]*(?:\.\d+)?)\s*(?P<maximum_k>[kK])?)?"
    r"\s*(?P<period>/\s*(?:h(?:ou)?r|day|week|month|mo|year|yr)|"
    r"per\s+(?:hour|day|week|month|year)|hourly|daily|weekly|monthly|annually|annual)",
    re.IGNORECASE,
)


class TheMuseSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedSalary:
    minimum: Decimal
    maximum: Decimal
    currency: str
    period: str


class TheMuseSource(BaseSource):
    name = "the_muse"
    display_name = "The Muse"
    opportunity_kind = OpportunityKind.EMPLOYMENT
    deactivate_missing_listings = False

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_key: str | None = None,
        categories: Sequence[str] = ("Software Engineering",),
        levels: Sequence[str] = ("Entry Level", "Mid Level"),
        location: str = "Flexible / Remote",
        request_timeout_seconds: float = 30.0,
        max_pages: int = 5,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key.strip() if api_key else None
        self._categories = tuple(value.strip() for value in categories if value.strip())
        self._levels = tuple(value.strip() for value in levels if value.strip())
        self._location = location.strip()
        self._request_timeout_seconds = request_timeout_seconds
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
        if not _is_remote(payload.get("locations")):
            raise TheMuseSourceError("The Muse listing is not marked as remote.")

        description_html = _required_string(payload.get("contents"), "contents")
        description = html_to_text(description_html)
        salary = parse_salary(description)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("name"), "name"),
            company=_company_name(payload.get("company")),
            description=description,
            location_text=_location_text(payload.get("locations")),
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(description),
            salary_min=salary.minimum if salary else None,
            salary_max=salary.maximum if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            published_at=_datetime(payload.get("publication_date")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        yielded = 0

        for page in range(self._max_pages):
            if yielded >= self._max_items:
                return
            payload = await self._request_page(client, page)
            jobs = payload.get("results")
            if not isinstance(jobs, list):
                raise TheMuseSourceError("The Muse response is missing the results list.")

            for item in jobs:
                if not isinstance(item, dict) or not _is_remote(item.get("locations")):
                    continue
                external_id = _optional_string(item.get("id"))
                source_url = _source_url(item.get("refs"))
                if external_id is None or source_url is None or external_id in seen:
                    continue
                seen.add(external_id)
                yielded += 1
                yield RawListing(
                    external_id=external_id,
                    source_url=source_url,
                    payload=item,
                )
                if yielded >= self._max_items:
                    return

            page_count = _integer(payload.get("page_count"))
            if not jobs or page_count is None or page + 1 >= page_count:
                return

    async def _request_page(
        self,
        client: httpx.AsyncClient,
        page: int,
    ) -> Mapping[str, Any]:
        params: list[tuple[str, str | int | float | bool | None]] = [
            ("page", page),
            ("descending", "true"),
            ("location", self._location),
        ]
        params.extend(("category", category) for category in self._categories)
        params.extend(("level", level) for level in self._levels)
        if self._api_key:
            params.append(("api_key", self._api_key))
        query_params = httpx.QueryParams(params)

        try:
            response = await client.get(self._api_url, params=query_params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as error:
            raise TheMuseSourceError(
                f"The Muse returned HTTP {error.response.status_code}."
            ) from None
        except httpx.HTTPError:
            raise TheMuseSourceError("The Muse request failed due to a network error.") from None
        except ValueError:
            raise TheMuseSourceError("The Muse returned invalid JSON.") from None
        if not isinstance(payload, Mapping):
            raise TheMuseSourceError("The Muse returned a non-object JSON response.")
        return payload


def parse_salary(value: str | None) -> ParsedSalary | None:
    if not value:
        return None
    matches = [match for match in SALARY_PATTERN.finditer(value) if match.group("label")]
    if not matches:
        return None
    match = next(
        (
            item
            for item in matches
            if (item.group("label") or "").casefold() in {"base pay", "base salary"}
        ),
        matches[0],
    )
    minimum = _amount(match.group("minimum"), match.group("minimum_k"))
    maximum = _amount(match.group("maximum"), match.group("maximum_k"))
    if minimum is None:
        return None
    maximum = maximum if maximum is not None else minimum
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return ParsedSalary(
        minimum=minimum,
        maximum=maximum,
        currency=_currency(match.group("currency")),
        period=_period(match.group("period")),
    )


def _amount(value: str | None, thousands_marker: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if thousands_marker:
        amount *= Decimal("1000")
    return amount


def _currency(value: str) -> str:
    normalized = value.upper()
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(normalized, normalized)


def _period(value: str) -> str:
    normalized = re.sub(r"[\s/]", "", value.casefold())
    if normalized in {"hr", "hour", "perhour", "hourly"}:
        return "hour"
    if normalized in {"day", "perday", "daily"}:
        return "day"
    if normalized in {"week", "perweek", "weekly"}:
        return "week"
    if normalized in {"month", "mo", "permonth", "monthly"}:
        return "month"
    return "year"


def _employment_type(description: str) -> str | None:
    normalized = description.casefold()
    values = (
        ("full_time", r"(?<!\w)full[- ]time(?!\w)"),
        ("part_time", r"(?<!\w)part[- ]time(?!\w)"),
        ("contractor", r"(?<!\w)(?:contractor|contract position)(?!\w)"),
        ("temporary", r"(?<!\w)temporary(?!\w)"),
        ("internship", r"(?<!\w)internship(?!\w)"),
    )
    return next((name for name, pattern in values if re.search(pattern, normalized)), None)


def _location_text(value: Any) -> str:
    names = _location_names(value)
    physical_locations = [name for name in names if name.casefold() not in REMOTE_LOCATION_NAMES]
    return "; ".join(physical_locations) if physical_locations else "Remote"


def _is_remote(value: Any) -> bool:
    return any(name.casefold() in REMOTE_LOCATION_NAMES for name in _location_names(value))


def _location_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            name = _optional_string(item.get("name"))
        else:
            name = _optional_string(item)
        if name and name not in names:
            names.append(name)
    return names


def _source_url(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _optional_string(value.get("landing_page"))


def _company_name(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise TheMuseSourceError("The Muse job is missing company.")
    return _required_string(value.get("name"), "company.name")


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


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    parsed = _optional_string(value)
    if parsed is None:
        raise TheMuseSourceError(f"The Muse job is missing {field_name}.")
    return parsed


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed or None
