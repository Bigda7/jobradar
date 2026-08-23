from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode
from jobradar.domain.models import NormalizedOpportunity, RawListing
from jobradar.sources.base import BaseSource

DEFAULT_LISTINGS: tuple[dict[str, Any], ...] = (
    {
        "id": "mock-001",
        "url": "https://example.com/jobs/junior-full-stack-developer",
        "title": "Junior Full-Stack Developer",
        "company": "Example Labs",
        "description": "Build remote web applications with React, Django, and PostgreSQL.",
        "location": "Remote Europe",
        "work_mode": "remote",
        "employment_type": "full_time",
        "salary_min": "1200",
        "salary_max": "1800",
        "salary_currency": "USD",
        "salary_period": "month",
        "published_at": "2026-08-22T09:00:00+00:00",
    },
    {
        "id": "mock-002",
        "url": "https://example.com/jobs/junior-react-developer",
        "title": "Junior React Developer",
        "company": "Remote Studio",
        "description": "Create accessible React interfaces and integrate REST APIs.",
        "location": "Remote Worldwide",
        "work_mode": "remote",
        "employment_type": "full_time",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "published_at": "2026-08-22T10:00:00+00:00",
    },
)


class MockSource(BaseSource):
    name = "mock"
    display_name = "Mock Source"
    opportunity_kind = OpportunityKind.EMPLOYMENT

    def __init__(self, listings: Sequence[dict[str, Any]] | None = None) -> None:
        self._listings = tuple(listings or DEFAULT_LISTINGS)

    async def fetch(self) -> AsyncIterator[RawListing]:
        for item in self._listings:
            yield RawListing(
                external_id=str(item["id"]),
                source_url=str(item["url"]),
                payload=dict(item),
            )

    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        payload = raw_listing.payload
        published_at = payload.get("published_at")
        return NormalizedOpportunity(
            kind=self.opportunity_kind,
            title=str(payload["title"]),
            company=_optional_string(payload.get("company")),
            description=_optional_string(payload.get("description")),
            location_text=_optional_string(payload.get("location")),
            work_mode=WorkMode(str(payload.get("work_mode", WorkMode.UNKNOWN.value))),
            employment_type=_optional_string(payload.get("employment_type")),
            contract_type=_optional_string(payload.get("contract_type")),
            salary_min=payload.get("salary_min"),
            salary_max=payload.get("salary_max"),
            salary_currency=_optional_string(payload.get("salary_currency")),
            salary_period=_optional_string(payload.get("salary_period")),
            published_at=(
                datetime.fromisoformat(str(published_at)).astimezone(UTC) if published_at else None
            ),
        )


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None
