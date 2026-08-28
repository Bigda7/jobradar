from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jobradar.domain.enums import OpportunityKind, WorkMode


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    kind: OpportunityKind
    title: str
    company: str | None
    description: str | None
    location_text: str | None
    work_mode: WorkMode
    employment_type: str | None
    contract_type: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str | None
    raw_data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    reasons: tuple[str, ...]
    concerns: tuple[str, ...]
    matched_skills: tuple[str, ...] = ()
