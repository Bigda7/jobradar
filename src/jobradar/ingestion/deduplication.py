import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from jobradar.domain.models import NormalizedOpportunity
from jobradar.domain.normalization import (
    normalize_company_identity,
    normalize_text,
    normalize_title_identity,
)
from jobradar.ingestion.canonical import refresh_opportunity_from_best_listing

MAX_PUBLICATION_GAP = timedelta(days=7)
MIN_DESCRIPTION_LENGTH = 40
MIN_DESCRIPTION_TERMS = 8
MAX_DESCRIPTION_LENGTH_RATIO = 1.5
MIN_SHARED_TERM_RATIO = 0.8


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


def is_confident_duplicate(
    left: Opportunity | NormalizedOpportunity,
    right: Opportunity | NormalizedOpportunity,
) -> bool:
    """Only merge cross-postings with corroborating vacancy details."""
    title = normalize_title_identity(left.title)
    company = normalize_company_identity(left.company)
    if not title or not company:
        return False
    if title != normalize_title_identity(right.title) or company != normalize_company_identity(
        right.company
    ):
        return False

    location = normalize_text(left.location_text)
    if not location or location != normalize_text(right.location_text):
        return False
    if str(left.work_mode) == "unknown" or str(left.work_mode) != str(right.work_mode):
        return False
    if left.published_at is None or right.published_at is None:
        return False
    if abs(_as_utc(left.published_at) - _as_utc(right.published_at)) > MAX_PUBLICATION_GAP:
        return False

    left_description = normalize_text(left.description)
    right_description = normalize_text(right.description)
    if min(len(left_description), len(right_description)) < MIN_DESCRIPTION_LENGTH:
        return False
    if max(len(left_description), len(right_description)) > MAX_DESCRIPTION_LENGTH_RATIO * min(
        len(left_description), len(right_description)
    ):
        return False
    left_terms = set(re.findall(r"\w+", left_description))
    right_terms = set(re.findall(r"\w+", right_description))
    if min(len(left_terms), len(right_terms)) < MIN_DESCRIPTION_TERMS:
        return False
    if (
        len(left_terms & right_terms) / min(len(left_terms), len(right_terms))
        < MIN_SHARED_TERM_RATIO
    ):
        return False

    if left.salary_currency and right.salary_currency:
        if left.salary_currency.casefold() != right.salary_currency.casefold():
            return False
    if left.salary_period and right.salary_period:
        if left.salary_period.casefold() != right.salary_period.casefold():
            return False
    if (
        left.salary_min is not None
        and right.salary_max is not None
        and left.salary_min > right.salary_max
    ) or (
        right.salary_min is not None
        and left.salary_max is not None
        and right.salary_min > left.salary_max
    ):
        return False
    return True


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
            listing_rows = (
                await session.execute(select(Listing.opportunity_id, Listing.source_id))
            ).all()
            source_ids_by_opportunity: dict[int, set[int]] = {}
            for opportunity_id, source_id in listing_rows:
                source_ids_by_opportunity.setdefault(opportunity_id, set()).add(source_id)
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
                merged_group = False
                merged_ids: set[int] = set()
                for index, primary in enumerate(group):
                    if primary.id in merged_ids:
                        continue
                    for duplicate in group[index + 1 :]:
                        if duplicate.id in merged_ids:
                            continue
                        primary_sources = source_ids_by_opportunity.get(primary.id, set())
                        duplicate_sources = source_ids_by_opportunity.get(duplicate.id, set())
                        if (
                            not primary_sources
                            or not duplicate_sources
                            or not primary_sources.isdisjoint(duplicate_sources)
                            or not is_confident_duplicate(primary, duplicate)
                        ):
                            continue
                        await self._merge_opportunity(session, primary, duplicate)
                        primary_sources.update(duplicate_sources)
                        merged_ids.add(duplicate.id)
                        summary.merged_opportunities += 1
                        merged_group = True
                if merged_group:
                    summary.duplicate_groups += 1
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
                existing.message_text = delivery.message_text
                existing.source_url = delivery.source_url
                existing.next_attempt_at = delivery.next_attempt_at
            elif (
                delivery.status == existing.status
                and delivery.status in {DeliveryStatus.QUEUED.value, DeliveryStatus.FAILED.value}
                and existing.message_text is None
                and existing.source_url is None
                and delivery.message_text is not None
                and delivery.source_url is not None
            ):
                existing.message_text = delivery.message_text
                existing.source_url = delivery.source_url
                existing.next_attempt_at = delivery.next_attempt_at
                existing.attempts = max(existing.attempts, delivery.attempts)
                existing.last_error = delivery.last_error
            await session.execute(
                update(TelegramOpportunityMessage)
                .where(TelegramOpportunityMessage.delivery_id == delivery.id)
                .values(delivery_id=existing.id)
            )
            await session.delete(delivery)
        await session.flush()


def _delivery_priority(status: str) -> int:
    priorities = {
        DeliveryStatus.SENT.value: 5,
        DeliveryStatus.PENDING.value: 4,
        DeliveryStatus.FAILED.value: 3,
        DeliveryStatus.QUEUED.value: 2,
        DeliveryStatus.SKIPPED_PAUSED.value: 1,
    }
    return priorities.get(status, 0)
