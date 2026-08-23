from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource
from jobradar.sources.detail_cache import can_reuse_detail, discovery_fingerprint, polite_delay

DEFAULT_API_BASE_URL = "https://www.freelance.cz/api/ui"
DEFAULT_WEB_BASE_URL = "https://www.freelance.cz"
DEFAULT_CATEGORY = "programovani-it"
USER_AGENT = "JobRadar/0.9 (personal job aggregator)"


class FreelanceCzSourceError(RuntimeError):
    pass


class FreelanceCzSource(BaseSource):
    name = "freelance_cz"
    display_name = "Freelance.cz"
    opportunity_kind = OpportunityKind.FREELANCE_PROJECT

    def __init__(
        self,
        api_base_url: str = DEFAULT_API_BASE_URL,
        web_base_url: str = DEFAULT_WEB_BASE_URL,
        category: str = DEFAULT_CATEGORY,
        request_timeout_seconds: float = 30.0,
        page_size: int = 25,
        max_pages: int = 2,
        max_items: int = 25,
        remote_only: bool = True,
        detail_cache_ttl_seconds: int = 86400,
        detail_request_delay_seconds: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._web_base_url = web_base_url.rstrip("/")
        self._category = category
        self._request_timeout_seconds = request_timeout_seconds
        self._page_size = page_size
        self._max_pages = max_pages
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
                "Accept": "application/json",
                "Accept-Language": "cs,en;q=0.8",
            },
            timeout=timeout,
        ) as client:
            async for listing in self._fetch_with_client(client):
                yield listing

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        hourly_rate = _decimal(payload.get("hourRate"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(payload.get("title"), "title"),
            company=None,
            description=_optional_string(payload.get("description")),
            location_text="Remote",
            work_mode=WorkMode.REMOTE,
            contract_type="hourly" if hourly_rate is not None else "fixed",
            salary_min=hourly_rate,
            salary_max=hourly_rate,
            salary_currency="CZK" if hourly_rate is not None else None,
            salary_period="hour" if hourly_rate is not None else None,
            published_at=_datetime(payload.get("createdAt")),
        )

    async def _fetch_with_client(self, client: httpx.AsyncClient) -> AsyncIterator[RawListing]:
        seen: set[str] = set()
        yielded = 0
        for page in range(1, self._max_pages + 1):
            response = await self._request_json(
                client,
                "POST",
                f"{self._api_base_url}/projects/search",
                json_body=self._search_body(page),
            )
            projects = response.get("projects")
            if not isinstance(projects, list):
                raise FreelanceCzSourceError(
                    "Freelance.cz search response is missing the projects list."
                )
            for summary in projects:
                if not isinstance(summary, Mapping):
                    continue
                external_id = _optional_string(summary.get("id"))
                if external_id is None or external_id in seen:
                    continue
                seen.add(external_id)
                if self._remote_only and summary.get("remote") != "remote":
                    continue
                stable_summary = dict(summary)
                fingerprint = discovery_fingerprint(stable_summary)
                cached = self.cached_listing(external_id)
                cached_summary = (
                    cached.payload.get("_search_summary") if cached is not None else None
                )
                cached_fingerprint = (
                    discovery_fingerprint(dict(cached_summary))
                    if isinstance(cached_summary, Mapping)
                    else None
                )
                now = datetime.now(UTC)
                if can_reuse_detail(
                    cached,
                    fingerprint=fingerprint,
                    cached_fingerprint=cached_fingerprint,
                    required_fields=("description",),
                    ttl_seconds=self._detail_cache_ttl_seconds,
                    now=now,
                ):
                    assert cached is not None
                    detail = dict(cached.payload)
                    detail_fetched_at = cached.detail_fetched_at
                else:
                    await polite_delay(self._detail_request_delay_seconds)
                    detail = await self._request_json(
                        client,
                        "GET",
                        f"{self._api_base_url}/projects/{external_id}",
                    )
                    detail_fetched_at = datetime.now(UTC)
                if detail.get("visibility") != "online":
                    continue
                if detail.get("type") != "project_only":
                    continue
                if self._remote_only and detail.get("remote") != "remote":
                    continue
                detail["_search_summary"] = stable_summary
                detail["description_truncated"] = detail.get("descriptionTruncated") is True
                detail["jobs"] = _skill_jobs(detail.get("skillTags"))
                detail["currency"] = {"code": "CZK"}
                detail_path = _optional_string(summary.get("linkToDetail"))
                source_url = urljoin(
                    f"{self._web_base_url}/",
                    detail_path or f"project/{external_id}",
                )
                yield RawListing(
                    external_id=external_id,
                    source_url=source_url,
                    payload=detail,
                    detail_fetched_at=detail_fetched_at,
                )
                yielded += 1
                if yielded >= self._max_items:
                    return

            pagination = response.get("pagination")
            if not isinstance(pagination, Mapping):
                break
            pages_count = _integer(pagination.get("pagesCount"))
            if pages_count is None or page >= pages_count:
                break

    def _search_body(self, page: int) -> dict[str, Any]:
        return {
            "keywords": [],
            "projectsFilter": {
                "remotePreference": [
                    {"id": "remote", "checked": True, "name": "remote"},
                ]
            },
            "pagination": {
                "currentPage": page,
                "pageSize": self._page_size,
                "sortBy": "default",
                "asc": False,
            },
            "category": self._category,
            "locale": "cs-CZ",
        }

    @staticmethod
    async def _request_json(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, url, json=json_body)
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise FreelanceCzSourceError(f"Freelance.cz request failed: {error}") from error
        if not isinstance(value, dict):
            raise FreelanceCzSourceError("Freelance.cz returned a non-object JSON response.")
        return value


def _skill_jobs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _optional_string(item.get("skillName"))
        if name:
            result.append({"name": name})
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


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise FreelanceCzSourceError(f"Freelance.cz project is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = " ".join(str(value).replace("\u00a0", " ").split())
    return result or None
