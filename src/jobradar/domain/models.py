from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from jobradar.domain.enums import OpportunityKind, WorkMode


class RawListing(BaseModel):
    model_config = ConfigDict(frozen=True)

    external_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    payload: dict[str, Any]
    detail_fetched_at: datetime | None = None
    is_available: bool = True


class NormalizedOpportunity(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: OpportunityKind = OpportunityKind.EMPLOYMENT
    title: str = Field(min_length=1, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    description: str | None = None
    location_text: str | None = Field(default=None, max_length=500)
    work_mode: WorkMode = WorkMode.UNKNOWN
    employment_type: str | None = Field(default=None, max_length=100)
    contract_type: str | None = Field(default=None, max_length=100)
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    salary_period: str | None = Field(default=None, max_length=50)
    published_at: datetime | None = None
