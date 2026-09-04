from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    NotificationDelivery,
    Opportunity,
    OpportunityUserState,
    TelegramOpportunityMessage,
)
from jobradar.domain.enums import DeliveryStatus, OpportunityDisposition, OpportunityKind
from jobradar.domain.normalization import (
    normalize_company_identity,
    normalize_title_identity,
)
from jobradar.ingestion.canonical import refresh_opportunity_from_best_listing


@dataclass(slots=True)
class DeduplicationSummary:
    duplicate_groups: int = 0
    merged_opportunities: int = 0


@dataclass(frozen=True, slots=True)
class DuplicateAuditGroup:
    normalized_title: str
    normalized_company: str
    opportunity_ids: tuple[int, ...]
    titles: tuple[str, ...]
    companies: tuple[str | None, ...]


@dataclass(frozen=True, slots=True)
class DeduplicationAudit:
    groups: tuple[DuplicateAuditGroup, ...]

    @property
    def candidate_groups(self) -> int:
        return len(self.groups)

    @property
    def candidate_opportunities(self) -> int:
        return sum(len(group.opportunity_ids) for group in self.groups)


class CrossSourceDeduplicationService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def merge_existing(self) -> DeduplicationSummary:
        summary = DeduplicationSummary()
        async with self._session_factory() as session, session.begin():
            opportunities = (
                await session.scalars(
                    select(Opportunity)
                    .where(Opportunity.kind == OpportunityKind.EMPLOYMENT.value)
                    .order_by(Opportunity.id.asc())
                )
            ).all()
            groups: dict[tuple[str, str], list[Opportunity]] = {}
            for opportunity in opportunities:
                title_key = normalize_title_identity(opportunity.title)
                company_key = normalize_company_identity(opportunity.company)
                if not title_key or not company_key:
                    continue
                groups.setdefault((title_key, company_key), []).append(opportunity)

            for group in groups.values():
                if len(group) < 2:
                    continue
                summary.duplicate_groups += 1
                primary = group[0]
                for duplicate in group[1:]:
                    await self._merge_opportunity(session, primary, duplicate)
                    summary.merged_opportunities += 1
        return summary

    async def audit_existing(self) -> DeduplicationAudit:
        async with self._session_factory() as session:
            opportunities = (
                await session.scalars(
                    select(Opportunity)
                    .where(Opportunity.kind == OpportunityKind.EMPLOYMENT.value)
                    .order_by(Opportunity.id.asc())
                )
            ).all()

        groups: dict[tuple[str, str], list[Opportunity]] = {}
        for opportunity in opportunities:
            title_key = normalize_title_identity(opportunity.title)
            company_key = normalize_company_identity(opportunity.company)
            if not title_key or not company_key:
                continue
            groups.setdefault((title_key, company_key), []).append(opportunity)

        audit_groups = tuple(
            DuplicateAuditGroup(
                normalized_title=identity[0],
                normalized_company=identity[1],
                opportunity_ids=tuple(item.id for item in group),
                titles=tuple(item.title for item in group),
                companies=tuple(item.company for item in group),
            )
            for identity, group in groups.items()
            if len(group) > 1
        )
        return DeduplicationAudit(groups=audit_groups)

    async def _merge_opportunity(
        self,
        session: AsyncSession,
        primary: Opportunity,
        duplicate: Opportunity,
    ) -> None:
        await self._merge_user_state(session, primary.id, duplicate.id)
        await self._merge_deliveries(session, primary.id, duplicate.id)
        await session.execute(
            delete(MatchEvaluation).where(MatchEvaluation.opportunity_id == duplicate.id)
        )
        await session.execute(
            update(TelegramOpportunityMessage)
            .where(TelegramOpportunityMessage.opportunity_id == duplicate.id)
            .values(opportunity_id=primary.id)
        )
        await session.execute(
            update(Listing)
            .where(Listing.opportunity_id == duplicate.id)
            .values(opportunity_id=primary.id)
        )
        await session.execute(delete(Opportunity).where(Opportunity.id == duplicate.id))
        await session.flush()
        await refresh_opportunity_from_best_listing(session, primary.id)

    @staticmethod
    async def _merge_user_state(
        session: AsyncSession,
        primary_id: int,
        duplicate_id: int,
    ) -> None:
        primary_state = await session.get(OpportunityUserState, primary_id)
        duplicate_state = await session.get(OpportunityUserState, duplicate_id)
        if duplicate_state is None:
            return
        dispositions = {
            primary_state.disposition if primary_state is not None else None,
            duplicate_state.disposition,
        }
        if OpportunityDisposition.FAVORITE.value in dispositions:
            disposition = OpportunityDisposition.FAVORITE.value
        elif OpportunityDisposition.HIDDEN.value in dispositions:
            disposition = OpportunityDisposition.HIDDEN.value
        else:
            disposition = OpportunityDisposition.NEW.value
        if primary_state is None:
            session.add(
                OpportunityUserState(
                    opportunity_id=primary_id,
                    disposition=disposition,
                )
            )
        else:
            primary_state.disposition = disposition
        await session.delete(duplicate_state)
        await session.flush()

    @staticmethod
    async def _merge_deliveries(
        session: AsyncSession,
        primary_id: int,
        duplicate_id: int,
    ) -> None:
        deliveries = (
            await session.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == duplicate_id
                )
            )
        ).all()
        for delivery in deliveries:
            existing = await session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == primary_id,
                    NotificationDelivery.profile_id == delivery.profile_id,
                    NotificationDelivery.channel == delivery.channel,
                    NotificationDelivery.event_key == delivery.event_key,
                )
            )
            if existing is None:
                delivery.opportunity_id = primary_id
                continue
            if _delivery_priority(delivery.status) > _delivery_priority(existing.status):
                existing.status = delivery.status
                existing.attempts = max(existing.attempts, delivery.attempts)
                existing.last_error = delivery.last_error
                existing.sent_at = delivery.sent_at
            await session.delete(delivery)
        await session.flush()


def _delivery_priority(status: str) -> int:
    priorities = {
        DeliveryStatus.SENT.value: 4,
        DeliveryStatus.SKIPPED_PAUSED.value: 3,
        DeliveryStatus.FAILED.value: 2,
        DeliveryStatus.PENDING.value: 1,
    }
    return priorities.get(status, 0)
