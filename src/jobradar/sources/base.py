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


class BaseSource(ABC):
    name: str
    display_name: str
    opportunity_kind: OpportunityKind
    deactivate_missing_listings: bool = False

    def prime_listing_cache(self, listings: dict[str, CachedListing]) -> None:
        self._cached_listings = dict(listings)

    def cached_listing(self, external_id: str) -> CachedListing | None:
        return getattr(self, "_cached_listings", {}).get(external_id)

    @abstractmethod
    def fetch(self) -> AsyncIterator[RawListing]:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw_listing: RawListing) -> NormalizedOpportunity:
        raise NotImplementedError
