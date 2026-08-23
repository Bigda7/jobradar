from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, Opportunity, OpportunityUserState
from jobradar.domain.enums import OpportunityDisposition, OpportunityKind
from jobradar.ingestion.canonical import refresh_opportunity_from_best_listing


@dataclass(slots=True)
class StaleExpirationSummary:
    expired_employment: int = 0
    expired_freelance: int = 0
    protected_favorites: int = 0

    @property
    def expired_total(self) -> int:
        return self.expired_employment + self.expired_freelance


class StaleExpirationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def expire_stale(
        self,
        *,
        employment_days: int,
        freelance_days: int,
        now: datetime | None = None,
    ) -> StaleExpirationSummary:
        if employment_days < 1 or freelance_days < 1:
            raise ValueError("Stale expiration limits must be positive numbers of days.")

        reference_time = _as_utc(now or datetime.now(UTC))
        cutoffs = {
            OpportunityKind.EMPLOYMENT.value: reference_time - timedelta(days=employment_days),
            OpportunityKind.FREELANCE_PROJECT.value: reference_time
            - timedelta(days=freelance_days),
        }
        summary = StaleExpirationSummary()

        async with self._session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    select(
                        Listing,
                        Opportunity.kind,
                        OpportunityUserState.disposition,
                    )
                    .join(Opportunity, Opportunity.id == Listing.opportunity_id)
                    .outerjoin(
                        OpportunityUserState,
                        OpportunityUserState.opportunity_id == Opportunity.id,
                    )
                    .where(Listing.is_active.is_(True))
                    .order_by(Listing.id.asc())
                )
            ).all()
            affected_opportunity_ids: set[int] = set()
            for listing, opportunity_kind, disposition in rows:
                cutoff = cutoffs.get(opportunity_kind)
                if cutoff is None:
                    continue
                effective_date = listing.published_at or listing.first_seen_at
                if _as_utc(effective_date) > cutoff:
                    continue
                if disposition == OpportunityDisposition.FAVORITE.value:
                    summary.protected_favorites += 1
                    continue

                listing.is_active = False
                affected_opportunity_ids.add(listing.opportunity_id)
                if opportunity_kind == OpportunityKind.FREELANCE_PROJECT.value:
                    summary.expired_freelance += 1
                else:
                    summary.expired_employment += 1

            if affected_opportunity_ids:
                await session.flush()
                for opportunity_id in sorted(affected_opportunity_ids):
                    await refresh_opportunity_from_best_listing(session, opportunity_id)

        return summary


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
