from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.base import utc_now
from jobradar.db.models import Listing, MatchEvaluation, Opportunity, Source
from jobradar.domain.enums import OpportunityKind, OpportunityStatus, WorkMode
from jobradar.ingestion.canonical import canonical_listing_order
from jobradar.matching.profile import SearchProfile
from jobradar.matching.scorer import MatchCandidate, score_candidate


@dataclass(slots=True)
class MatchingSummary:
    evaluated: int = 0
    unchanged: int = 0


class MatchingService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def evaluate(
        self,
        profile: SearchProfile,
        *,
        force: bool = False,
    ) -> MatchingSummary:
        summary = MatchingSummary()
        async with self._session_factory() as session:
            opportunity_ids = (
                await session.scalars(
                    select(Opportunity.id)
                    .join(Listing, Listing.opportunity_id == Opportunity.id)
                    .join(Source, Source.id == Listing.source_id)
                    .where(
                        Opportunity.status == OpportunityStatus.ACTIVE.value,
                        Listing.is_active.is_(True),
                        Source.enabled.is_(True),
                    )
                    .distinct()
                )
            ).all()

        for opportunity_id in opportunity_ids:
            changed = await self._evaluate_opportunity(opportunity_id, profile, force=force)
            if changed:
                summary.evaluated += 1
            else:
                summary.unchanged += 1
        return summary

    async def _evaluate_opportunity(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        *,
        force: bool = False,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            opportunity = await session.get(Opportunity, opportunity_id)
            if opportunity is None:
                return False
            listing = await session.scalar(
                select(Listing)
                .join(Source, Source.id == Listing.source_id)
                .where(Listing.opportunity_id == opportunity_id, Listing.is_active.is_(True))
                .where(Source.enabled.is_(True))
                .order_by(*canonical_listing_order())
                .limit(1)
            )
            if listing is None:
                return False
            evaluation = await session.scalar(
                select(MatchEvaluation).where(
                    MatchEvaluation.opportunity_id == opportunity_id,
                    MatchEvaluation.profile_id == profile.profile_id,
                    MatchEvaluation.rules_version == profile.rules_version,
                )
            )
            if (
                not force
                and evaluation is not None
                and evaluation.listing_content_hash == listing.content_hash
            ):
                return False

            result = score_candidate(
                MatchCandidate(
                    kind=OpportunityKind(opportunity.kind),
                    title=opportunity.title,
                    company=opportunity.company,
                    description=opportunity.description,
                    location_text=opportunity.location_text,
                    work_mode=WorkMode(opportunity.work_mode),
                    employment_type=opportunity.employment_type,
                    contract_type=opportunity.contract_type,
                    salary_min=opportunity.salary_min,
                    salary_max=opportunity.salary_max,
                    salary_currency=opportunity.salary_currency,
                    salary_period=opportunity.salary_period,
                    raw_data=listing.raw_data,
                ),
                profile,
            )
            if evaluation is None:
                evaluation = MatchEvaluation(
                    opportunity_id=opportunity_id,
                    profile_id=profile.profile_id,
                    rules_version=profile.rules_version,
                    listing_content_hash=listing.content_hash,
                    score=result.score,
                    reasons=list(result.reasons),
                    concerns=list(result.concerns),
                    matched_skills=list(result.matched_skills),
                )
                session.add(evaluation)
            else:
                evaluation.listing_content_hash = listing.content_hash
                evaluation.score = result.score
                evaluation.reasons = list(result.reasons)
                evaluation.concerns = list(result.concerns)
                evaluation.matched_skills = list(result.matched_skills)
                evaluation.evaluated_at = utc_now()
            return True
