from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jobradar.domain.enums import OpportunityKind
from jobradar.domain.models import NormalizedOpportunity, RawListing


@dataclass(frozen=True, slots=True)
class CachedListing:
    payload: dict[str, Any]
    detail_fetched_at: datetime | None


@dataclass(slots=True)
class SourceRunMetrics:
    candidate_count: int = 0
    filtered_count: int = 0
    detail_failure_count: int = 0
    page_count: int = 0
    limit_reached: bool = False


class BaseSource(ABC):
    name: str
    display_name: str
    opportunity_kind: OpportunityKind
    deactivate_missing_listings: bool = False

    def begin_run(self) -> None:
        self._run_warnings: list[str] = []
        self._run_metrics = SourceRunMetrics()

    def prime_listing_cache(self, listings: dict[str, CachedListing]) -> None:
        self._cached_listings = dict(listings)

    def cached_listing(self, external_id: str) -> CachedListing | None:
        return getattr(self, "_cached_listings", {}).get(external_id)

    def report_warning(self, message: str) -> None:
        warnings = getattr(self, "_run_warnings", [])
        warnings.append(message)
        self._run_warnings = warnings

    def consume_warnings(self) -> tuple[str, ...]:
        warnings = tuple(getattr(self, "_run_warnings", []))
        self._run_warnings = []
        return warnings

    def record_candidates(self, count: int) -> None:
        if count < 0:
            raise ValueError("Candidate count cannot be negative.")
        self._metrics().candidate_count += count

    def record_filtered(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("Filtered count cannot be negative.")
        self._metrics().filtered_count += count

    def record_detail_failure(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("Detail failure count cannot be negative.")
        self._metrics().detail_failure_count += count

    def record_page(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("Page count cannot be negative.")
        self._metrics().page_count += count

    def mark_limit_reached(self) -> None:
        self._metrics().limit_reached = True

    def consume_run_metrics(self) -> SourceRunMetrics:
        metrics = self._metrics()
        self._run_metrics = SourceRunMetrics()
        return metrics

    def _metrics(self) -> SourceRunMetrics:
        metrics = getattr(self, "_run_metrics", None)
        if metrics is None:
            metrics = SourceRunMetrics()
            self._run_metrics = metrics
        return metrics

    @abstractmethod
    def fetch(self) -> AsyncIterator[RawListing]:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        raise NotImplementedError
