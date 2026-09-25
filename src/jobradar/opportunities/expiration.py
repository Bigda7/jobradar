from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only
from sqlalchemy.sql.elements import ColumnElement

from jobradar.db.models import Listing, Opportunity, OpportunityUserState
from jobradar.domain.enums import OpportunityDisposition, OpportunityKind
from jobradar.ingestion.canonical import refresh_opportunity_from_best_listing


@dataclass(slots=True)
class StaleExpirationSummary:
    expired_employment: int = 0
    expired_freelance: int = 0
    archived_favorites: int = 0
    restored_recent: int = 0

    @property
    def expired_total(self) -> int:
        return self.expired_employment + self.expired_freelance


class StaleExpirationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = 500,
    ) -> None:
        if batch_size < 1:
            raise ValueError("Expiration batch size must be positive.")
        self._session_factory = session_factory
        self._batch_size = batch_size

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
            last_listing_id = 0
            while True:
                rows = (
                    await session.execute(
                        select(
                            Listing,
                            Opportunity.kind,
                            OpportunityUserState.disposition,
                        )
                        .options(
                            load_only(
                                Listing.id,
                                Listing.opportunity_id,
                                Listing.published_at,
                                Listing.first_seen_at,
                                Listing.is_active,
                                Listing.archive_reason,
                                Listing.archived_at,
                            )
                        )
                        .join(Opportunity, Opportunity.id == Listing.opportunity_id)
                        .outerjoin(
                            OpportunityUserState,
                            OpportunityUserState.opportunity_id == Opportunity.id,
                        )
                        .where(
                            Listing.id > last_listing_id,
                            _expiration_candidate_filter(cutoffs),
                        )
                        .order_by(Listing.id.asc())
                        .limit(self._batch_size)
                    )
                ).all()
                if not rows:
                    break

                affected_opportunity_ids: set[int] = set()
                for listing, opportunity_kind, disposition in rows:
                    if not listing.is_active:
                        listing.is_active = True
                        listing.archive_reason = None
                        listing.archived_at = None
                        summary.restored_recent += 1
                        affected_opportunity_ids.add(listing.opportunity_id)
                        continue

                    listing.is_active = False
                    listing.archive_reason = "expired"
                    listing.archived_at = reference_time
                    affected_opportunity_ids.add(listing.opportunity_id)
                    if disposition == OpportunityDisposition.FAVORITE.value:
                        summary.archived_favorites += 1
                    if opportunity_kind == OpportunityKind.FREELANCE_PROJECT.value:
                        summary.expired_freelance += 1
                    else:
                        summary.expired_employment += 1

                await session.flush()
                for opportunity_id in sorted(affected_opportunity_ids):
                    await refresh_opportunity_from_best_listing(session, opportunity_id)
                last_listing_id = rows[-1][0].id

        return summary


def _expiration_candidate_filter(cutoffs: dict[str, datetime]) -> ColumnElement[bool]:
    effective_date = func.coalesce(Listing.published_at, Listing.first_seen_at)
    restorable = and_(
        Listing.is_active.is_(False),
        or_(Listing.archive_reason.is_(None), Listing.archive_reason == "missing"),
    )
    return or_(
        and_(
            Opportunity.kind == OpportunityKind.EMPLOYMENT.value,
            or_(
                and_(
                    Listing.is_active.is_(True),
                    effective_date <= cutoffs[OpportunityKind.EMPLOYMENT.value],
                ),
                and_(
                    restorable,
                    effective_date > cutoffs[OpportunityKind.EMPLOYMENT.value],
                ),
            ),
        ),
        and_(
            Opportunity.kind == OpportunityKind.FREELANCE_PROJECT.value,
            or_(
                and_(
                    Listing.is_active.is_(True),
                    effective_date <= cutoffs[OpportunityKind.FREELANCE_PROJECT.value],
                ),
                and_(
                    restorable,
                    effective_date > cutoffs[OpportunityKind.FREELANCE_PROJECT.value],
                ),
            ),
        ),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
