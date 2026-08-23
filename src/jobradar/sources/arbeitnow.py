from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_URL = "https://www.arbeitnow.com/api/job-board-api"
USER_AGENT = "JobRadar/1.2 (personal job aggregator)"


class ArbeitnowSourceError(RuntimeError):
    pass


class ArbeitnowSource(BaseSource):
    name = "arbeitnow"
    display_name = "Arbeitnow"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        request_timeout_seconds: float = 30.0,
        max_pages: int = 3,
        max_items: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
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
        description_html = _required_string(payload.get("description"), "description")
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=_required_string(payload.get("company_name"), "company_name"),
            description=html_to_text(description_html),
            location_text=_optional_string(payload.get("location")) or "Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_employment_type(payload.get("job_types")),
            published_at=_timestamp(payload.get("created_at")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        yielded = 0
        next_url: str | None = self._api_url

        for page in range(1, self._max_pages + 1):
            if next_url is None or yielded >= self._max_items:
                return
            try:
                response = await client.get(next_url, params={"page": page})
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise ArbeitnowSourceError(f"Arbeitnow request failed: {error}") from error
            if not isinstance(payload, Mapping):
                raise ArbeitnowSourceError("Arbeitnow returned a non-object JSON response.")
            jobs = payload.get("data")
            if not isinstance(jobs, list):
                raise ArbeitnowSourceError("Arbeitnow response is missing the data list.")

            for item in jobs:
                if not isinstance(item, dict) or item.get("remote") is not True:
                    continue
                external_id = _optional_string(item.get("slug"))
                source_url = _optional_string(item.get("url"))
                if external_id is None or source_url is None or external_id in seen:
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

            links = payload.get("links")
            next_url = _optional_string(links.get("next")) if isinstance(links, Mapping) else None


def _employment_type(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    types = [
        text.casefold().replace("-", "_").replace(" ", "_")
        for item in value
        if (text := _optional_string(item)) is not None
    ]
    return ",".join(types) or None


def _timestamp(value: Any) -> datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise ArbeitnowSourceError(f"Arbeitnow listing is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
