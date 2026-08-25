import html
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.ats_common import (
    USER_AGENT,
    AtsSourceError,
    company_name,
    company_payload,
    external_id,
    infer_employment_type,
    optional_string,
    parse_datetime,
    parse_text_salary,
    required_string,
    strict_remote,
)
from jobradar.sources.ats_config import AtsCompany
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_BASE_URL = "https://boards-api.greenhouse.io"


class GreenhouseSource(BaseSource):
    name = "greenhouse"
    display_name = "Greenhouse"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        companies: Sequence[AtsCompany],
        api_base_url: str = DEFAULT_API_BASE_URL,
        request_timeout_seconds: float = 30.0,
        max_items_per_company: int = 500,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._companies = tuple(companies)
        self._api_base_url = api_base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._max_items_per_company = max_items_per_company
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
        location = _location_name(payload)
        if not _is_remote(payload):
            raise AtsSourceError("Greenhouse listing is not strictly remote.")
        description_html = required_string(payload.get("content"), "content")
        description = html_to_text(html.unescape(description_html))
        salary = parse_text_salary(description)
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=required_string(payload.get("title"), "title"),
            company=company_name(payload),
            description=description,
            location_text=location or "Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=infer_employment_type(description),
            salary_min=salary.minimum if salary else None,
            salary_max=salary.maximum if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            published_at=parse_datetime(payload.get("updated_at")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        for company in self._companies:
            url = f"{self._api_base_url}/v1/boards/{quote(company.identifier)}/jobs"
            try:
                response = await client.get(url, params={"content": "true"})
                response.raise_for_status()
                document = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise AtsSourceError(
                    f"Greenhouse request failed for {company.name}: {error}"
                ) from error
            if not isinstance(document, Mapping) or not isinstance(document.get("jobs"), list):
                raise AtsSourceError(f"Greenhouse returned invalid jobs for {company.name}.")
            accepted = 0
            for item in document["jobs"]:
                if not isinstance(item, Mapping) or not _is_remote(item):
                    continue
                source_url = optional_string(item.get("absolute_url"))
                if source_url is None:
                    continue
                identifier = external_id(company, item.get("id"), source_url)
                if identifier in seen:
                    continue
                seen.add(identifier)
                yield RawListing(
                    external_id=identifier,
                    source_url=source_url,
                    payload=company_payload(item, company),
                )
                accepted += 1
                if accepted >= self._max_items_per_company:
                    break


def _is_remote(payload: Mapping[str, Any]) -> bool:
    return strict_remote(
        payload.get("location"),
        payload.get("offices"),
    )


def _location_name(payload: Mapping[str, Any]) -> str | None:
    location = payload.get("location")
    if not isinstance(location, Mapping):
        return None
    return optional_string(location.get("name"))
