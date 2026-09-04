from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class HealthResponse(BaseModel):
    status: str


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    title: str
    company: str | None
    description: str | None
    location_text: str | None
    work_mode: str
    employment_type: str | None
    contract_type: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str | None
    published_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    source_url: HttpUrl
    source_name: str
    source_display_name: str

    _normalize_datetimes = field_validator(
        "published_at",
        "first_seen_at",
        "last_seen_at",
        mode="before",
    )(_as_utc)


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class MatchResponse(JobResponse):
    score: int
    reasons: list[str]
    concerns: list[str]
    matched_skills: list[str]
    rules_version: str


class MatchListResponse(BaseModel):
    items: list[MatchResponse]
    total: int
    minimum_score: int
    limit: int
    offset: int


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    display_name: str
    opportunity_kind: str
    enabled: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_run_status: str | None = None
    last_discovered_count: int | None = None
    last_created_count: int | None = None
    last_updated_count: int | None = None
    last_unchanged_count: int | None = None
    last_deactivated_count: int | None = None
    last_error_count: int | None = None

    _normalize_datetimes = field_validator(
        "last_run_at",
        "last_success_at",
        mode="before",
    )(_as_utc)
