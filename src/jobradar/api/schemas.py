from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class MatchResponse(JobResponse):
    source_url: str
    score: int
    reasons: list[str]
    concerns: list[str]
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
