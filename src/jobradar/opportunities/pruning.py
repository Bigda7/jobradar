from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, MatchEvaluation, Opportunity, Source, SourceRun
from jobradar.ingestion.canonical import refresh_opportunity_from_best_listing


@dataclass(frozen=True, slots=True)
class SourcePruningSummary:
    matched_sources: int
    deleted_source_runs: int
    deleted_listings: int
    deleted_opportunities: int
    preserved_shared_opportunities: int
    applied: bool


class SourcePruningService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def prune(
        self,
        source_names: Collection[str],
        *,
        apply: bool = False,
    ) -> SourcePruningSummary:
        normalized_names = tuple(
            sorted({name.strip().casefold() for name in source_names if name.strip()})
        )
        if not normalized_names:
            return SourcePruningSummary(0, 0, 0, 0, 0, apply)

        async with self._session_factory() as session, session.begin():
            source_ids = tuple(
                await session.scalars(select(Source.id).where(Source.name.in_(normalized_names)))
            )
            if not source_ids:
                return SourcePruningSummary(0, 0, 0, 0, 0, apply)

            deleted_source_runs = int(
                await session.scalar(
                    select(func.count())
                    .select_from(SourceRun)
                    .where(SourceRun.source_id.in_(source_ids))
                )
                or 0
            )

            deleted_listings = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Listing)
                    .where(Listing.source_id.in_(source_ids))
                )
                or 0
            )
            affected_opportunity_ids = tuple(
                await session.scalars(
                    select(distinct(Listing.opportunity_id)).where(
                        Listing.source_id.in_(source_ids)
                    )
                )
            )
            retained_listing_exists = (
                select(Listing.id)
                .where(
                    Listing.opportunity_id == Opportunity.id,
                    Listing.source_id.not_in(source_ids),
                )
                .exists()
            )
            orphan_opportunity_ids = tuple(
                await session.scalars(
                    select(Opportunity.id).where(
                        Opportunity.id.in_(affected_opportunity_ids),
                        ~retained_listing_exists,
                    )
                )
            )
            orphan_ids = set(orphan_opportunity_ids)
            shared_opportunity_ids = tuple(
                opportunity_id
                for opportunity_id in affected_opportunity_ids
                if opportunity_id not in orphan_ids
            )

            if apply:
                await session.execute(delete(Listing).where(Listing.source_id.in_(source_ids)))
                if orphan_opportunity_ids:
                    await session.execute(
                        delete(Opportunity).where(Opportunity.id.in_(orphan_opportunity_ids))
                    )
                if shared_opportunity_ids:
                    await session.execute(
                        delete(MatchEvaluation).where(
                            MatchEvaluation.opportunity_id.in_(shared_opportunity_ids)
                        )
                    )
                    await session.flush()
                    for opportunity_id in shared_opportunity_ids:
                        await refresh_opportunity_from_best_listing(session, opportunity_id)
                await session.execute(delete(SourceRun).where(SourceRun.source_id.in_(source_ids)))
                await session.execute(delete(Source).where(Source.id.in_(source_ids)))

            return SourcePruningSummary(
                matched_sources=len(source_ids),
                deleted_source_runs=deleted_source_runs,
                deleted_listings=deleted_listings,
                deleted_opportunities=len(orphan_opportunity_ids),
                preserved_shared_opportunities=len(shared_opportunity_ids),
                applied=apply,
            )
