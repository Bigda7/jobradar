from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Self
from urllib.parse import quote

import httpx

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource

DEFAULT_API_BASE_URL = "https://www.freelancer.com/api/projects/0.1"
DEFAULT_WEB_BASE_URL = "https://www.freelancer.com"
USER_AGENT = "JobRadar/0.4 (personal opportunity aggregator)"


class FreelancerApiError(RuntimeError):
    pass


class FreelancerAuthenticationError(FreelancerApiError):
    pass


class FreelancerRateLimitError(FreelancerApiError):
    pass


class FreelancerSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FreelancerSearchPage:
    projects: tuple[dict[str, Any], ...]
    users: dict[str, dict[str, Any]]
    total_count: int


class FreelancerApiClient:
    def __init__(
        self,
        oauth_token: str,
        api_base_url: str = DEFAULT_API_BASE_URL,
        request_timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        token = oauth_token.strip()
        if not token:
            raise ValueError("Freelancer OAuth token must not be empty.")
        self._oauth_token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._client = client
        self._owns_client = False

    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(self._request_timeout_seconds),
            )
            self._owns_client = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    async def search_active_projects(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
    ) -> FreelancerSearchPage:
        if self._client is None:
            raise RuntimeError("FreelancerApiClient must be used as an async context manager.")

        params: dict[str, str | int | bool] = {
            "query": query,
            "limit": limit,
            "offset": offset,
            "sort_field": "time_updated",
            "reverse_sort": True,
            "or_search_query": True,
            "include_contests": False,
            "full_description": True,
            "job_details": True,
            "user_details": True,
            "user_employer_reputation": True,
            "user_status": True,
        }
        endpoint = f"{self._api_base_url}/projects/active/"
        headers = {
            "Accept": "application/json",
            "Freelancer-OAuth-V1": self._oauth_token,
            "User-Agent": USER_AGENT,
        }

        try:
            response = await self._client.get(endpoint, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise FreelancerApiError(f"Freelancer request failed: {error}") from error

        payload = _response_payload(response)
        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise FreelancerAuthenticationError(
                _api_error_message(payload, "Freelancer rejected the OAuth token.")
            )
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = response.headers.get("Retry-After")
            suffix = f" Retry after {retry_after} seconds." if retry_after else ""
            raise FreelancerRateLimitError(
                _api_error_message(payload, "Freelancer rate limit was reached.") + suffix
            )
        if response.is_error:
            raise FreelancerApiError(
                _api_error_message(
                    payload,
                    f"Freelancer returned HTTP {response.status_code}.",
                )
            )

        if payload.get("status") != "success":
            raise FreelancerApiError(
                _api_error_message(payload, "Freelancer returned an unsuccessful response.")
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise FreelancerApiError("Freelancer response did not contain a result object.")

        projects = result.get("projects")
        if not isinstance(projects, list):
            raise FreelancerApiError("Freelancer response did not contain a projects list.")
        project_items = tuple(item for item in projects if isinstance(item, dict))

        raw_users = result.get("users")
        users = (
            {
                str(user_id): dict(user)
                for user_id, user in raw_users.items()
                if isinstance(user, Mapping)
            }
            if isinstance(raw_users, Mapping)
            else {}
        )
        return FreelancerSearchPage(
            projects=project_items,
            users=users,
            total_count=_integer(result.get("total_count"), default=len(project_items)),
        )


class FreelancerSource(BaseSource):
    name = "freelancer"
    display_name = "Freelancer.com"
    opportunity_kind = OpportunityKind.FREELANCE_PROJECT

    def __init__(
        self,
        api_client: FreelancerApiClient,
        search_queries: Sequence[str],
        web_base_url: str = DEFAULT_WEB_BASE_URL,
        page_size: int = 50,
        max_pages_per_query: int = 2,
    ) -> None:
        queries = tuple(query.strip() for query in search_queries if query.strip())
        if not queries:
            raise ValueError("FreelancerSource requires at least one search query.")
        if page_size < 1:
            raise ValueError("Freelancer page size must be positive.")
        if max_pages_per_query < 1:
            raise ValueError("Freelancer max pages must be positive.")
        self._api_client = api_client
        self._search_queries = queries
        self._web_base_url = web_base_url.rstrip("/")
        self._page_size = page_size
        self._max_pages_per_query = max_pages_per_query

    async def fetch(self) -> AsyncIterator[RawListing]:
        seen_ids: set[str] = set()
        async with self._api_client:
            for query in self._search_queries:
                offset = 0
                for page_number in range(1, self._max_pages_per_query + 1):
                    self.record_page()
                    page = await self._api_client.search_active_projects(
                        query,
                        limit=self._page_size,
                        offset=offset,
                    )
                    if not page.projects:
                        break
                    self.record_candidates(len(page.projects))

                    for project in page.projects:
                        external_id = _optional_string(project.get("id"))
                        if external_id is None or external_id in seen_ids:
                            self.record_filtered()
                            continue
                        if _is_local_or_contest(project):
                            self.record_filtered()
                            continue

                        payload = dict(project)
                        owner = page.users.get(str(project.get("owner_id")))
                        if owner is not None:
                            payload["_owner"] = owner
                        yield RawListing(
                            external_id=external_id,
                            source_url=_project_url(
                                project,
                                external_id=external_id,
                                web_base_url=self._web_base_url,
                            ),
                            payload=payload,
                            is_available=not _is_terminal_project(project),
                        )
                        seen_ids.add(external_id)

                    offset += len(page.projects)
                    if len(page.projects) < self._page_size or offset >= page.total_count:
                        break
                    if page_number >= self._max_pages_per_query:
                        self.mark_limit_reached()

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        project = raw_listing.payload
        if _is_local_or_contest(project):
            raise FreelancerSourceError("Local projects and contests are not supported.")

        contract_type = _contract_type(project.get("type"))
        salary_min, salary_max = _budget(project.get("budget"))
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=_required_string(project.get("title"), "title"),
            company=_owner_name(project),
            description=_optional_string(
                project.get("description") or project.get("preview_description")
            ),
            location_text="Remote",
            work_mode=WorkMode.REMOTE,
            employment_type=None,
            contract_type=contract_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=_currency_code(project.get("currency")),
            salary_period="hour" if contract_type == "hourly" else "project",
            published_at=_timestamp(project.get("submitdate") or project.get("time_submitted")),
        )


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise FreelancerApiError("Freelancer returned a non-JSON response.") from error
    if not isinstance(payload, dict):
        raise FreelancerApiError("Freelancer returned an invalid JSON response.")
    return payload


def _api_error_message(payload: Mapping[str, Any], fallback: str) -> str:
    message = _optional_string(payload.get("message")) or fallback
    error_code = _optional_string(payload.get("error_code"))
    request_id = _optional_string(payload.get("request_id"))
    details = [message]
    if error_code is not None:
        details.append(f"Code: {error_code}.")
    if request_id is not None:
        details.append(f"Request ID: {request_id}.")
    return " ".join(details)[:2000]


def _project_url(
    project: Mapping[str, Any],
    *,
    external_id: str,
    web_base_url: str,
) -> str:
    seo_url = _optional_string(project.get("seo_url"))
    if seo_url is None:
        return f"{web_base_url}/projects/{quote(external_id, safe='')}"
    if seo_url.startswith(("https://", "http://")):
        return seo_url
    return f"{web_base_url}/projects/{quote(seo_url.strip('/'), safe='/')}"


def _is_local_or_contest(project: Mapping[str, Any]) -> bool:
    upgrades = project.get("upgrades")
    local_upgrade = upgrades.get("local") if isinstance(upgrades, Mapping) else None
    project_type = (_optional_string(project.get("type")) or "").casefold()
    return any(
        (
            _truthy(project.get("local")),
            _truthy(local_upgrade),
            _truthy(project.get("contest")),
            project_type in {"local", "contest"},
        )
    )


def _is_terminal_project(project: Mapping[str, Any]) -> bool:
    terminal_states = {
        "cancelled",
        "canceled",
        "closed",
        "complete",
        "completed",
        "ended",
        "expired",
        "rejected",
    }
    for key in ("status", "state", "project_status", "frontend_project_status"):
        value = _optional_string(project.get(key))
        if value is not None and value.casefold() in terminal_states:
            return True
    return any(
        _truthy(project.get(key)) for key in ("closed", "cancelled", "canceled", "ended", "expired")
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "yes"}
    return False


def _owner_name(project: Mapping[str, Any]) -> str | None:
    owner = project.get("_owner")
    if not isinstance(owner, Mapping):
        return None
    for key in ("display_name", "public_name", "username"):
        value = _optional_string(owner.get(key))
        if value is not None:
            return value
    return None


def _contract_type(value: Any) -> str | None:
    project_type = _optional_string(value)
    if project_type is None:
        return None
    normalized = project_type.casefold()
    if normalized in {"fixed", "hourly"}:
        return normalized
    return normalized


def _budget(value: Any) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(value, Mapping):
        return None, None
    return _decimal(value.get("minimum")), _decimal(value.get("maximum"))


def _currency_code(value: Any) -> str | None:
    if isinstance(value, Mapping):
        code = _optional_string(value.get("code"))
    else:
        code = _optional_string(value)
    return code.upper() if code is not None and len(code) == 3 else None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _integer(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _required_string(value: Any, field_name: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise FreelancerSourceError(f"Freelancer project is missing {field_name}.")
    return result


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None
