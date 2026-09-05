from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.domain.normalization import normalize_text
from jobradar.sources.ats_common import (
    USER_AGENT,
    AtsSalary,
    AtsSourceError,
    company_name,
    company_payload,
    currency_code,
    decimal_amount,
    employment_type,
    external_id,
    optional_string,
    parse_datetime,
    required_string,
    salary_period,
    strict_remote,
)
from jobradar.sources.ats_config import AtsCompany
from jobradar.sources.base import BaseSource
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_BASE_URL = "https://api.lever.co"


class LeverSource(BaseSource):
    name = "lever"
    display_name = "Lever"
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
        if not _is_remote(payload):
            raise AtsSourceError("Lever listing is not strictly remote.")
        description = optional_string(payload.get("descriptionPlain"))
        if description is None:
            description = html_to_text(required_string(payload.get("description"), "description"))
        categories = payload.get("categories")
        category_data = categories if isinstance(categories, Mapping) else {}
        salary = _salary(payload.get("salaryRange"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=required_string(payload.get("text"), "text"),
            company=company_name(payload),
            description=description,
            location_text=optional_string(category_data.get("location")) or "Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=employment_type(category_data.get("commitment")),
            salary_min=salary.minimum if salary else None,
            salary_max=salary.maximum if salary else None,
            salary_currency=salary.currency if salary else None,
            salary_period=salary.period if salary else None,
            published_at=parse_datetime(payload.get("createdAt")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        for company in self._companies:
            url = f"{self._api_base_url}/v0/postings/{quote(company.identifier)}"
            self.record_page()
            try:
                response = await client.get(url, params={"mode": "json"})
                response.raise_for_status()
                document = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise AtsSourceError(f"Lever request failed for {company.name}: {error}") from error
            if not isinstance(document, list):
                raise AtsSourceError(f"Lever returned invalid postings for {company.name}.")
            self.record_candidates(len(document))
            accepted = 0
            for item in document:
                if not isinstance(item, Mapping) or not _is_remote(item):
                    self.record_filtered()
                    continue
                source_url = optional_string(item.get("hostedUrl"))
                if source_url is None:
                    self.record_filtered()
                    continue
                identifier = external_id(company, item.get("id"), source_url)
                if identifier in seen:
                    self.record_filtered()
                    continue
                seen.add(identifier)
                yield RawListing(
                    external_id=identifier,
                    source_url=source_url,
                    payload=company_payload(item, company),
                )
                accepted += 1
                if accepted >= self._max_items_per_company:
                    self.mark_limit_reached()
                    break


def _is_remote(payload: Mapping[str, Any]) -> bool:
    workplace_type = normalize_text(optional_string(payload.get("workplaceType")))
    if workplace_type:
        return workplace_type == "remote"
    return strict_remote(payload.get("text"), payload.get("categories"))


def _salary(value: Any) -> AtsSalary | None:
    if not isinstance(value, Mapping):
        return None
    minimum = decimal_amount(value.get("min"))
    maximum = decimal_amount(value.get("max"))
    currency = optional_string(value.get("currency"))
    if minimum is None or maximum is None or currency is None:
        return None
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return AtsSalary(
        minimum=minimum,
        maximum=maximum,
        currency=currency_code(currency),
        period=salary_period(value.get("interval")),
    )
