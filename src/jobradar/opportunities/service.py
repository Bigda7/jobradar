from dataclasses import dataclass
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Listing, MatchEvaluation, Opportunity, OpportunityUserState, Source
from jobradar.domain.enums import OpportunityDisposition
from jobradar.ingestion.canonical import canonical_source_link_order
from jobradar.matching.profile import SearchProfile


@dataclass(frozen=True, slots=True)
class FavoriteOpportunity:
    opportunity_id: int
    title: str
    company: str | None
    source_url: str | None
    score: int | None


@dataclass(frozen=True, slots=True)
class OpportunityStats:
    collected: int
    evaluated: int
    matched: int
    filtered: int
    favorites: int
    hidden: int


class OpportunityStateService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def toggle_favorite(self, opportunity_id: int) -> OpportunityDisposition | None:
        async with self._session_factory() as session, session.begin():
            opportunity = await session.get(Opportunity, opportunity_id)
            if opportunity is None:
                return None
            state = await session.get(OpportunityUserState, opportunity_id)
            if state is None:
                state = OpportunityUserState(
                    opportunity_id=opportunity_id,
                    disposition=OpportunityDisposition.FAVORITE.value,
                )
                session.add(state)
                return OpportunityDisposition.FAVORITE
            if state.disposition == OpportunityDisposition.FAVORITE.value:
                state.disposition = OpportunityDisposition.NEW.value
                return OpportunityDisposition.NEW
            state.disposition = OpportunityDisposition.FAVORITE.value
            return OpportunityDisposition.FAVORITE

    async def hide(self, opportunity_id: int) -> bool:
        async with self._session_factory() as session, session.begin():
            opportunity = await session.get(Opportunity, opportunity_id)
            if opportunity is None:
                return False
            state = await session.get(OpportunityUserState, opportunity_id)
            if state is None:
                session.add(
                    OpportunityUserState(
                        opportunity_id=opportunity_id,
                        disposition=OpportunityDisposition.HIDDEN.value,
                    )
                )
            else:
                state.disposition = OpportunityDisposition.HIDDEN.value
            return True

    async def restore(self, opportunity_id: int) -> OpportunityDisposition | None:
        async with self._session_factory() as session, session.begin():
            opportunity = await session.get(Opportunity, opportunity_id)
            if opportunity is None:
                return None
            state = await session.get(OpportunityUserState, opportunity_id)
            if state is None:
                return OpportunityDisposition.NEW
            if state.disposition == OpportunityDisposition.HIDDEN.value:
                state.disposition = OpportunityDisposition.NEW.value
                return OpportunityDisposition.NEW
            return OpportunityDisposition(state.disposition)

    async def reset_hidden(self) -> int:
        async with self._session_factory() as session, session.begin():
            opportunity_ids = tuple(
                (
                    await session.scalars(
                        select(OpportunityUserState.opportunity_id).where(
                            OpportunityUserState.disposition == OpportunityDisposition.HIDDEN.value
                        )
                    )
                ).all()
            )
            if not opportunity_ids:
                return 0
            await session.execute(
                delete(OpportunityUserState).where(
                    OpportunityUserState.opportunity_id.in_(opportunity_ids)
                )
            )
            return len(opportunity_ids)

    async def get_disposition(self, opportunity_id: int) -> OpportunityDisposition:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(OpportunityUserState.disposition).where(
                    OpportunityUserState.opportunity_id == opportunity_id
                )
            )
        return OpportunityDisposition(value or OpportunityDisposition.NEW.value)

    async def favorite_ids(self) -> set[int]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(OpportunityUserState.opportunity_id).where(
                    OpportunityUserState.disposition == OpportunityDisposition.FAVORITE.value
                )
            )
            return set(values.all())

    async def source_url(self, opportunity_id: int) -> str | None:
        async with self._session_factory() as session:
            return cast(
                str | None,
                await session.scalar(
                    select(Listing.source_url)
                    .join(Source, Source.id == Listing.source_id)
                    .where(
                        Listing.opportunity_id == opportunity_id,
                        Listing.is_active.is_(True),
                        Source.enabled.is_(True),
                    )
                    .order_by(*canonical_source_link_order())
                    .limit(1)
                ),
            )

    async def list_favorites(self, profile: SearchProfile) -> list[FavoriteOpportunity]:
        async with self._session_factory() as session:
            states = (
                await session.scalars(
                    select(OpportunityUserState)
                    .where(
                        OpportunityUserState.disposition == OpportunityDisposition.FAVORITE.value
                    )
                    .order_by(OpportunityUserState.updated_at.desc())
                )
            ).all()
            result: list[FavoriteOpportunity] = []
            for state in states:
                opportunity = await session.get(Opportunity, state.opportunity_id)
                if opportunity is None:
                    continue
                source_url = await session.scalar(
                    select(Listing.source_url)
                    .join(Source, Source.id == Listing.source_id)
                    .where(
                        Listing.opportunity_id == opportunity.id,
                        Listing.is_active.is_(True),
                        Source.enabled.is_(True),
                    )
                    .order_by(*canonical_source_link_order())
                    .limit(1)
                )
                score = await session.scalar(
                    select(MatchEvaluation.score).where(
                        MatchEvaluation.opportunity_id == opportunity.id,
                        MatchEvaluation.profile_id == profile.profile_id,
                        MatchEvaluation.rules_version == profile.rules_version,
                    )
                )
                result.append(
                    FavoriteOpportunity(
                        opportunity_id=opportunity.id,
                        title=opportunity.title,
                        company=opportunity.company,
                        source_url=source_url,
                        score=score,
                    )
                )
            return result

    async def stats(self, profile: SearchProfile, minimum_score: int) -> OpportunityStats:
        async with self._session_factory() as session:
            collected = int(
                await session.scalar(select(func.count()).select_from(Opportunity)) or 0
            )
            evaluation_filter = (
                MatchEvaluation.profile_id == profile.profile_id,
                MatchEvaluation.rules_version == profile.rules_version,
            )
            evaluated = int(
                await session.scalar(
                    select(func.count()).select_from(MatchEvaluation).where(*evaluation_filter)
                )
                or 0
            )
            matched = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MatchEvaluation)
                    .where(*evaluation_filter, MatchEvaluation.score >= minimum_score)
                )
                or 0
            )
            favorites = await self._state_count(session, OpportunityDisposition.FAVORITE)
            hidden = await self._state_count(session, OpportunityDisposition.HIDDEN)
        return OpportunityStats(
            collected=collected,
            evaluated=evaluated,
            matched=matched,
            filtered=max(0, evaluated - matched),
            favorites=favorites,
            hidden=hidden,
        )

    @staticmethod
    async def _state_count(session: AsyncSession, disposition: OpportunityDisposition) -> int:
        value = await session.scalar(
            select(func.count())
            .select_from(OpportunityUserState)
            .where(OpportunityUserState.disposition == disposition.value)
        )
        return int(value or 0)
