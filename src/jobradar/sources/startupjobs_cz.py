from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.detail_cache import can_reuse_detail, discovery_fingerprint, polite_delay
from jobradar.sources.structured_data import html_to_text

DEFAULT_API_BASE_URL = "https://back.startupjobs.cz"
DEFAULT_WEB_BASE_URL = "https://www.startupjobs.cz"
DEFAULT_QUERIES = (
    "python django",
    "react javascript typescript",
    "fullstack frontend backend",
    "shopify liquid api",
)
USER_AGENT = "JobRadar/0.9 (personal job aggregator)"


class StartupJobsCzSourceError(RuntimeError):
    pass


class StartupJobsCzSource(BaseSource):
    name = "startupjobs_cz"
    display_name = "StartupJobs.cz"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(
        self,
        api_base_url: str = DEFAULT_API_BASE_URL,
        web_base_url: str = DEFAULT_WEB_BASE_URL,
        search_queries: tuple[str, ...] = DEFAULT_QUERIES,
        request_timeout_seconds: float = 30.0,
        page_size: int = 20,
        max_pages_per_query: int = 2,
        max_items: int = 20,
        remote_only: bool = True,
        detail_cache_ttl_seconds: int = 86400,
        detail_request_delay_seconds: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._web_base_url = web_base_url.rstrip("/")
        self._search_queries = search_queries
        self._request_timeout_seconds = request_timeout_seconds
        self._page_size = page_size
        self._max_pages_per_query = max_pages_per_query
        self._max_items = max_items
        self._remote_only = remote_only
        self._detail_cache_ttl_seconds = detail_cache_ttl_seconds
        self._detail_request_delay_seconds = detail_request_delay_seconds
        self._client = client

    async def fetch(self) -> AsyncIterator[RawListing]:
        if self._client is not None:
            async for listing in self._fetch_with_client(self._client):
                yield listing
            return

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/ld+json,application/json",
                "Accept-Language": "cs,en;q=0.8",
            },
            timeout=timeout,
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        salary_min, salary_max, salary_currency, salary_period = _salary(payload)
        description_html = _localized_string(payload.get("description"))
        search_summary = payload.get("_search_summary")
        company = None
        if isinstance(search_summary, Mapping):
            company_data = search_summary.get("company")
            if isinstance(company_data, Mapping):
                company = _optional_string(company_data.get("name"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_localized_string(payload.get("name"), "name"),
            company=company,
            description=html_to_text(description_html) if description_html else None,
            location_text="Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=_joined_ids(payload.get("hours")),
            contract_type=_joined_ids(payload.get("contracts")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            published_at=_datetime(payload.get("publishedAt")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        groups: list[list[dict[str, Any]]] = []
        for query in self._search_queries:
            query_results: list[dict[str, Any]] = []
            for page in range(1, self._max_pages_per_query + 1):
                response = await self._request_json(
                    client,
                    "POST",
                    f"{self._api_base_url}/api/search-offers",
                    params={"page": page},
                    json_body={"query": query, "locationPreferences": ["remote"]},
                )
                members = response.get("member")
                if not isinstance(members, list):
                    raise StartupJobsCzSourceError(
                        "StartupJobs.cz search response is missing the member list."
                    )
                query_results.extend(item for item in members if isinstance(item, dict))
                view = response.get("view")
                if not isinstance(view, Mapping) or not view.get("next"):
                    break
            groups.append(query_results)

        seen: set[str] = set()
        yielded = 0
        for summary in _round_robin(groups):
            external_id = _optional_string(summary.get("id"))
            if external_id is None or external_id in seen:
                continue
            seen.add(external_id)
            if self._remote_only and not _summary_is_fully_remote(summary):
                continue
            stable_summary = dict(summary)
            _remove_generated_ids(stable_summary)
            fingerprint = discovery_fingerprint(stable_summary)
            cached = self.cached_listing(external_id)
            cached_summary = cached.payload.get("_search_summary") if cached is not None else None
            cached_fingerprint = (
                discovery_fingerprint(dict(cached_summary))
                if isinstance(cached_summary, Mapping)
                else None
            )
            now = datetime.now(UTC)
            if cached is not None and can_reuse_detail(
                cached,
                fingerprint=fingerprint,
                cached_fingerprint=cached_fingerprint,
                required_fields=("description",),
                ttl_seconds=self._detail_cache_ttl_seconds,
                now=now,
            ):
                detail = dict(cached.payload)
                detail_fetched_at = cached.detail_fetched_at
            else:
                await polite_delay(self._detail_request_delay_seconds)
                detail = await self._request_json(
                    client,
                    "GET",
                    f"{self._api_base_url}/api/offers/{external_id}",
                )
                detail_fetched_at = datetime.now(UTC)
            if detail.get("state") != "published":
                continue
            if self._remote_only and not _detail_is_fully_remote(detail):
                continue
            _remove_generated_ids(detail)
            detail["_search_summary"] = stable_summary
            slug = _localized_string(detail.get("slug"))
            suffix = f"/{slug}" if slug else ""
            yield RawListing(
                external_id=external_id,
                source_url=f"{self._web_base_url}/nabidka/{external_id}{suffix}",
                payload=detail,
                detail_fetched_at=detail_fetched_at,
            )
            yielded += 1
            if yielded >= self._max_items:
                return

    @staticmethod
    async def _request_json(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        params: dict[str, int] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, url, params=params, json=json_body)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise StartupJobsCzSourceError(f"StartupJobs.cz request failed: {error}") from error
        if not isinstance(value, dict):
            raise StartupJobsCzSourceError("StartupJobs.cz returned a non-object JSON response.")
        return value


def _summary_is_fully_remote(summary: Mapping[str, Any]) -> bool:
    preferences = summary.get("locationPreferences")
    return isinstance(preferences, list) and preferences == ["remote"]


def _detail_is_fully_remote(detail: Mapping[str, Any]) -> bool:
    return _ids(detail.get("locationPreferences")) == ["remote"]


def _salary(
    payload: Mapping[str, Any],
) -> tuple[Decimal | None, Decimal | None, str | None, str | None]:
    salary = payload.get("salary")
    if not isinstance(salary, Mapping):
        return None, None, None, None
    minimum = _money_from_minor_units(salary.get("min"))
    maximum = _money_from_minor_units(salary.get("max"))
    currency = _optional_string(salary.get("currency"))
    period = _optional_string(salary.get("period"))
    return (
        minimum if minimum and minimum > 0 else None,
        maximum if maximum and maximum > 0 else None,
        currency.upper() if currency else None,
        period.casefold() if period else None,
    )


def _money_from_minor_units(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)) / Decimal("100")
    except (InvalidOperation, ValueError):
        return None


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        item_id = item.get("id") if isinstance(item, Mapping) else item
        normalized = _optional_string(item_id)
        if normalized:
            result.append(normalized.casefold())
    return result


def _joined_ids(value: Any) -> str | None:
    values = [item.replace("-", "_") for item in _ids(value)]
    return ",".join(values) or None


def _localized_string(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for locale in ("cs", "en"):
            localized = _optional_string(value.get(locale))
            if localized:
                return localized
        return None
    return _optional_string(value)


def _required_localized_string(value: Any, field_name: str) -> str:
    result = _localized_string(value)
    if result is None:
        raise StartupJobsCzSourceError(f"StartupJobs.cz offer is missing {field_name}.")
    return result


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


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _remove_generated_ids(value: Any) -> None:
    if isinstance(value, dict):
        generated_id = value.get("@id")
        if isinstance(generated_id, str) and generated_id.startswith("/api/.well-known/genid/"):
            value.pop("@id")
        for child in value.values():
            _remove_generated_ids(child)
    elif isinstance(value, list):
        for child in value:
            _remove_generated_ids(child)


def _round_robin(groups: list[list[dict[str, Any]]]) -> Iterable[dict[str, Any]]:
    index = 0
    while any(index < len(group) for group in groups):
        for group in groups:
            if index < len(group):
                yield group[index]
        index += 1
