from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import cast

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    NotificationDelivery,
    Opportunity,
    OpportunityUserState,
    Source,
    TelegramOpportunityMessage,
)
from jobradar.domain.enums import DeliveryStatus, OpportunityDisposition, OpportunityKind
from jobradar.ingestion.canonical import canonical_source_link_order
from jobradar.ingestion.link_filter import trusted_listing_condition
from jobradar.matching.profile import SearchProfile
from jobradar.notifications.currency import (
    CurrencyConversionError,
    ExchangeRateProvider,
    ExchangeRates,
    format_converted_range,
    format_original_range,
)
from jobradar.notifications.messages import TelegramMessageRegistry
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.telegram import (
    InlineKeyboardMarkup,
    TelegramClient,
    TelegramDeliveryError,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    opportunity_id: int
    kind: OpportunityKind
    title: str
    company: str | None
    location_text: str | None
    employment_type: str | None
    contract_type: str | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    salary_period: str | None
    first_seen_at: datetime
    source_display_name: str
    source_url: str
    content_hash: str
    score: int
    reasons: tuple[str, ...]
    concerns: tuple[str, ...]
    raw_data: dict[str, object]
    evaluated_at: datetime | None = None


@dataclass(slots=True)
class NotificationSummary:
    considered: int = 0
    sent: int = 0
    failed: int = 0
    skipped_historical: int = 0
    skipped_duplicate: int = 0
    skipped_paused: int = 0
    retry_deferred: int = 0


class NotificationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_client: TelegramClient,
        exchange_rate_provider: ExchangeRateProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._telegram_client = telegram_client
        self._exchange_rate_provider = exchange_rate_provider
        self._message_registry = TelegramMessageRegistry(session_factory)
        self._preferences = NotificationPreferenceService(session_factory)

    async def dispatch(
        self,
        profile: SearchProfile,
        minimum_score: int,
        max_messages: int,
        minimum_first_seen_at: datetime | None,
    ) -> NotificationSummary:
        summary = NotificationSummary()
        candidates = await self.load_candidates(profile, minimum_score)
        pause_state = await self._preferences.get_state(profile.profile_id, "telegram")
        if pause_state.is_paused:
            for candidate in candidates:
                event_key = _event_key(profile.rules_version, candidate.content_hash)
                if (
                    pause_state.paused_at is not None
                    and candidate.evaluated_at is not None
                    and _as_utc(candidate.evaluated_at) < _as_utc(pause_state.paused_at)
                ):
                    delivery = await self._get_delivery(
                        candidate.opportunity_id, profile, event_key
                    )
                    if delivery is None or delivery.status != DeliveryStatus.QUEUED.value:
                        summary.skipped_historical += 1
                        continue
                summary.considered += 1
                if await self._mark_skipped_paused(
                    candidate.opportunity_id,
                    profile,
                    event_key,
                ):
                    summary.skipped_paused += 1
                else:
                    summary.skipped_duplicate += 1
            return summary

        await self._recover_pending_deliveries()

        for candidate in candidates:
            if minimum_first_seen_at is not None and _as_utc(candidate.first_seen_at) < _as_utc(
                minimum_first_seen_at
            ):
                continue
            await self._queue_delivery(
                candidate.opportunity_id,
                profile,
                _event_key(profile.rules_version, candidate.content_hash),
            )

        rates: ExchangeRates | None = None
        if any(_has_published_amount(candidate) for candidate in candidates):
            if self._exchange_rate_provider is None:
                raise RuntimeError("Exchange rate provider is required for match dispatch.")
            try:
                rates = await self._exchange_rate_provider.fetch_rates()
            except CurrencyConversionError as error:
                logger.warning(
                    "currency_conversion_fallback_original",
                    candidates=min(len(candidates), max_messages),
                    error=str(error),
                )
        prioritized_candidates = sorted(
            candidates,
            key=lambda candidate: (
                minimum_first_seen_at is None
                or _as_utc(candidate.first_seen_at) >= _as_utc(minimum_first_seen_at)
            ),
        )
        for candidate in prioritized_candidates:
            if summary.sent >= max_messages:
                break
            summary.considered += 1
            event_key = _event_key(profile.rules_version, candidate.content_hash)
            delivery = await self._get_delivery(candidate.opportunity_id, profile, event_key)
            is_retry = delivery is not None and delivery.status in {
                DeliveryStatus.FAILED.value,
                DeliveryStatus.QUEUED.value,
            }
            if (
                minimum_first_seen_at is not None
                and _as_utc(candidate.first_seen_at) < _as_utc(minimum_first_seen_at)
                and not is_retry
            ):
                summary.skipped_historical += 1
                continue
            if (
                delivery is not None
                and delivery.status == DeliveryStatus.FAILED.value
                and delivery.next_attempt_at is not None
                and _as_utc(delivery.next_attempt_at) > datetime.now(UTC)
            ):
                summary.retry_deferred += 1
                continue

            if (
                delivery is not None
                and delivery.status == DeliveryStatus.FAILED.value
                and delivery.message_text is not None
                and delivery.source_url is not None
            ):
                message = delivery.message_text
                source_url = delivery.source_url
            else:
                try:
                    message = format_match_message(candidate, rates)
                except CurrencyConversionError as error:
                    summary.failed += 1
                    logger.warning(
                        "currency_conversion_failed",
                        opportunity_id=candidate.opportunity_id,
                        currency=candidate.salary_currency,
                        error=str(error),
                    )
                    continue
                source_url = candidate.source_url

            delivery_id = await self._claim_delivery(
                candidate.opportunity_id,
                profile,
                event_key,
                message_text=message,
                source_url=source_url,
            )
            if delivery_id is None:
                summary.skipped_duplicate += 1
                continue

            if await self._send_claimed_delivery(
                delivery_id, candidate.opportunity_id, message, source_url
            ):
                summary.sent += 1
            else:
                summary.failed += 1
        return summary

    async def retry_due(self, profile_id: str, max_messages: int) -> NotificationSummary:
        summary = NotificationSummary()
        if max_messages <= 0:
            return summary
        pause_state = await self._preferences.get_state(profile_id, "telegram")
        if pause_state.is_paused:
            return summary

        await self._recover_pending_deliveries()
        async with self._session_factory() as session:
            due_ids = list(
                await session.scalars(
                    select(NotificationDelivery.id)
                    .where(
                        NotificationDelivery.profile_id == profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.FAILED.value,
                        NotificationDelivery.message_text.is_not(None),
                        NotificationDelivery.source_url.is_not(None),
                        or_(
                            NotificationDelivery.next_attempt_at.is_(None),
                            NotificationDelivery.next_attempt_at <= datetime.now(UTC),
                        ),
                    )
                    .order_by(
                        NotificationDelivery.next_attempt_at,
                        NotificationDelivery.id,
                    )
                    .limit(max_messages)
                )
            )

        for delivery_id in due_ids:
            claimed = await self._claim_stored_delivery(delivery_id)
            if claimed is None:
                continue
            opportunity_id, message, source_url = claimed
            summary.considered += 1
            if await self._send_claimed_delivery(delivery_id, opportunity_id, message, source_url):
                summary.sent += 1
            else:
                summary.failed += 1
        return summary

    async def _send_claimed_delivery(
        self,
        delivery_id: int,
        opportunity_id: int,
        message: str,
        source_url: str,
    ) -> bool:
        try:
            message_id = await self._telegram_client.send_message(
                message,
                reply_markup=opportunity_keyboard(opportunity_id, source_url),
            )
            await self._message_registry.record(opportunity_id, message_id, delivery_id=delivery_id)
        except TelegramDeliveryError as error:
            attempts, next_attempt_at = await self._finish_delivery(
                delivery_id, sent=False, error=str(error)
            )
            logger.warning(
                "telegram_delivery_failed",
                opportunity_id=opportunity_id,
                attempts=attempts,
                next_attempt_at=next_attempt_at,
                error=str(error),
            )
            if attempts >= 3:
                logger.error(
                    "telegram_delivery_repeated_failure",
                    opportunity_id=opportunity_id,
                    attempts=attempts,
                    next_attempt_at=next_attempt_at,
                )
            return False
        await self._finish_delivery(delivery_id, sent=True, error=None)
        return True

    async def _recover_pending_deliveries(self) -> None:
        recorded_message_at = (
            select(TelegramOpportunityMessage.created_at)
            .where(
                TelegramOpportunityMessage.delivery_id == NotificationDelivery.id,
            )
            .order_by(TelegramOpportunityMessage.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        async with self._session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    select(NotificationDelivery, recorded_message_at).where(
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.PENDING.value,
                    )
                )
            ).all()
            recovered_sent = 0
            retry_scheduled = 0
            recovered_at = datetime.now(UTC)
            for delivery, message_created_at in rows:
                delivery.attempts += 1
                if message_created_at is not None:
                    delivery.status = DeliveryStatus.SENT.value
                    delivery.sent_at = message_created_at
                    delivery.last_error = None
                    delivery.next_attempt_at = None
                    recovered_sent += 1
                else:
                    delivery.status = DeliveryStatus.FAILED.value
                    delivery.last_error = "Interrupted before delivery confirmation."
                    delivery.next_attempt_at = recovered_at + _retry_delay(delivery.attempts)
                    retry_scheduled += 1
        if recovered_sent or retry_scheduled:
            logger.warning(
                "pending_telegram_deliveries_recovered",
                confirmed_sent=recovered_sent,
                retry_scheduled=retry_scheduled,
            )

    async def load_candidates(
        self,
        profile: SearchProfile,
        minimum_score: int,
        *,
        latest_first: bool = False,
        limit: int | None = None,
    ) -> list[NotificationCandidate]:
        async with self._session_factory() as session:
            hidden_state = select(OpportunityUserState.opportunity_id).where(
                OpportunityUserState.opportunity_id == MatchEvaluation.opportunity_id,
                OpportunityUserState.disposition == OpportunityDisposition.HIDDEN.value,
            )
            evaluations = (
                await session.scalars(
                    select(MatchEvaluation)
                    .where(
                        MatchEvaluation.profile_id == profile.profile_id,
                        MatchEvaluation.rules_version == profile.rules_version,
                        MatchEvaluation.score >= minimum_score,
                        ~hidden_state.exists(),
                    )
                    .order_by(MatchEvaluation.score.desc(), MatchEvaluation.opportunity_id.desc())
                )
            ).all()
            candidates: list[NotificationCandidate] = []
            for evaluation in evaluations:
                opportunity = await session.get(Opportunity, evaluation.opportunity_id)
                listing_row = (
                    await session.execute(
                        select(Listing, Source.display_name)
                        .join(Source, Source.id == Listing.source_id)
                        .where(
                            Listing.opportunity_id == evaluation.opportunity_id,
                            Listing.is_active.is_(True),
                            Source.enabled.is_(True),
                            trusted_listing_condition(),
                        )
                        .order_by(*canonical_source_link_order())
                        .limit(1)
                    )
                ).first()
                if opportunity is None or listing_row is None:
                    continue
                listing, source_display_name = listing_row
                candidates.append(
                    NotificationCandidate(
                        opportunity_id=opportunity.id,
                        kind=OpportunityKind(opportunity.kind),
                        title=opportunity.title,
                        company=opportunity.company,
                        location_text=opportunity.location_text,
                        employment_type=opportunity.employment_type,
                        contract_type=opportunity.contract_type,
                        salary_min=opportunity.salary_min,
                        salary_max=opportunity.salary_max,
                        salary_currency=opportunity.salary_currency,
                        salary_period=opportunity.salary_period,
                        first_seen_at=opportunity.first_seen_at,
                        source_display_name=source_display_name,
                        source_url=listing.source_url,
                        content_hash=listing.content_hash,
                        score=evaluation.score,
                        reasons=tuple(evaluation.reasons),
                        concerns=tuple(evaluation.concerns),
                        raw_data=listing.raw_data,
                        evaluated_at=evaluation.evaluated_at,
                    )
                )
            if latest_first:
                candidates.sort(
                    key=lambda item: (_as_utc(item.first_seen_at), item.opportunity_id),
                    reverse=True,
                )
            return candidates[:limit] if limit is not None else candidates

    async def _get_delivery(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        event_key: str,
    ) -> NotificationDelivery | None:
        async with self._session_factory() as session:
            delivery = await session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.event_key == event_key,
                )
            )
            if delivery is None:
                delivery = await session.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                    )
                )
            if delivery is None:
                delivery = await session.scalar(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.FAILED.value,
                    )
                    .order_by(NotificationDelivery.id.desc())
                    .limit(1)
                )
            return cast(NotificationDelivery | None, delivery)

    async def _queue_delivery(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        event_key: str,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.event_key == event_key,
                )
            )
            if existing is not None:
                return
            pending = await session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.status == DeliveryStatus.PENDING.value,
                )
            )
            if pending is not None:
                return
            queued = await session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                )
            )
            if queued is not None:
                queued.event_key = event_key
                return
            failed = await session.scalar(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.status == DeliveryStatus.FAILED.value,
                )
                .order_by(NotificationDelivery.id.desc())
                .limit(1)
            )
            if failed is not None:
                failed.event_key = event_key
                return
            handled = await session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.status.in_(
                        (
                            DeliveryStatus.SENT.value,
                            DeliveryStatus.SKIPPED_PAUSED.value,
                        )
                    ),
                )
            )
            if handled is None:
                session.add(
                    NotificationDelivery(
                        opportunity_id=opportunity_id,
                        profile_id=profile.profile_id,
                        channel="telegram",
                        event_key=event_key,
                        status=DeliveryStatus.QUEUED.value,
                    )
                )

    async def _claim_delivery(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        event_key: str,
        message_text: str | None = None,
        source_url: str | None = None,
    ) -> int | None:
        async with self._session_factory() as session, session.begin():
            delivery = await session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.event_key == event_key,
                )
            )
            if delivery is None:
                delivery = await session.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                    )
                )
                if delivery is not None:
                    delivery.event_key = event_key
            if delivery is None:
                delivery = await session.scalar(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.FAILED.value,
                    )
                    .order_by(NotificationDelivery.id.desc())
                    .limit(1)
                )
                if delivery is not None:
                    delivery.event_key = event_key
            handled_delivery_id = await session.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.status.in_(
                        (
                            DeliveryStatus.PENDING.value,
                            DeliveryStatus.SENT.value,
                            DeliveryStatus.SKIPPED_PAUSED.value,
                        )
                    ),
                )
            )
            if handled_delivery_id is not None:
                return None
            if delivery is None:
                delivery = NotificationDelivery(
                    opportunity_id=opportunity_id,
                    profile_id=profile.profile_id,
                    channel="telegram",
                    event_key=event_key,
                    status=DeliveryStatus.PENDING.value,
                    message_text=message_text,
                    source_url=source_url,
                )
                session.add(delivery)
                await session.flush()
                return delivery.id
            if delivery.status not in {
                DeliveryStatus.QUEUED.value,
                DeliveryStatus.FAILED.value,
            }:
                return None
            if (
                delivery.status == DeliveryStatus.FAILED.value
                and delivery.next_attempt_at is not None
                and _as_utc(delivery.next_attempt_at) > datetime.now(UTC)
            ):
                return None
            delivery.status = DeliveryStatus.PENDING.value
            delivery.last_error = None
            delivery.next_attempt_at = None
            if delivery.message_text is None or delivery.source_url is None:
                delivery.message_text = message_text
                delivery.source_url = source_url
            return delivery.id

    async def _claim_stored_delivery(self, delivery_id: int) -> tuple[int, str, str] | None:
        async with self._session_factory() as session, session.begin():
            delivery = await session.get(NotificationDelivery, delivery_id)
            if (
                delivery is None
                or delivery.status != DeliveryStatus.FAILED.value
                or delivery.message_text is None
                or delivery.source_url is None
                or (
                    delivery.next_attempt_at is not None
                    and _as_utc(delivery.next_attempt_at) > datetime.now(UTC)
                )
            ):
                return None
            delivery.status = DeliveryStatus.PENDING.value
            delivery.last_error = None
            delivery.next_attempt_at = None
            return delivery.opportunity_id, delivery.message_text, delivery.source_url

    async def _finish_delivery(
        self,
        delivery_id: int,
        sent: bool,
        error: str | None,
    ) -> tuple[int, datetime | None]:
        async with self._session_factory() as session, session.begin():
            delivery = await session.get(NotificationDelivery, delivery_id)
            if delivery is None:
                raise RuntimeError("Notification delivery disappeared before completion.")
            delivery.attempts += 1
            delivery.status = DeliveryStatus.SENT.value if sent else DeliveryStatus.FAILED.value
            delivery.last_error = error[:2000] if error else None
            finished_at = datetime.now(UTC)
            delivery.sent_at = finished_at if sent else None
            delivery.next_attempt_at = (
                None if sent else finished_at + _retry_delay(delivery.attempts)
            )
            return delivery.attempts, delivery.next_attempt_at

    async def _mark_skipped_paused(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        event_key: str,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            delivery = await session.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == opportunity_id,
                    NotificationDelivery.profile_id == profile.profile_id,
                    NotificationDelivery.channel == "telegram",
                    NotificationDelivery.event_key == event_key,
                )
            )
            if delivery is None:
                delivery = await session.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status == DeliveryStatus.QUEUED.value,
                    )
                )
                if delivery is not None:
                    delivery.event_key = event_key
            if delivery is None:
                handled_delivery_id = await session.scalar(
                    select(NotificationDelivery.id).where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.status.in_(
                            (
                                DeliveryStatus.PENDING.value,
                                DeliveryStatus.SENT.value,
                                DeliveryStatus.SKIPPED_PAUSED.value,
                            )
                        ),
                    )
                )
                if handled_delivery_id is not None:
                    return False
                session.add(
                    NotificationDelivery(
                        opportunity_id=opportunity_id,
                        profile_id=profile.profile_id,
                        channel="telegram",
                        event_key=event_key,
                        status=DeliveryStatus.SKIPPED_PAUSED.value,
                    )
                )
                return True
            if delivery.status in {
                DeliveryStatus.PENDING.value,
                DeliveryStatus.SENT.value,
                DeliveryStatus.SKIPPED_PAUSED.value,
            }:
                return False
            delivery.status = DeliveryStatus.SKIPPED_PAUSED.value
            delivery.last_error = None
            delivery.sent_at = None
            delivery.next_attempt_at = None
            return True


def format_match_message(
    candidate: NotificationCandidate,
    rates: ExchangeRates | None = None,
) -> str:
    if candidate.kind is OpportunityKind.FREELANCE_PROJECT:
        return _format_freelance_match_message(candidate, rates)
    return _format_employment_match_message(candidate, rates)


def opportunity_keyboard(
    opportunity_id: int,
    source_url: str,
    *,
    is_favorite: bool = False,
    is_hidden: bool = False,
) -> InlineKeyboardMarkup:
    link_button = {"text": "Ссылка", "url": source_url}
    if is_hidden:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Восстановить",
                        "callback_data": f"restore:{opportunity_id}",
                    },
                    link_button,
                ]
            ]
        }
    favorite_text = "В избранном" if is_favorite else "В избранное"
    return {
        "inline_keyboard": [
            [
                {"text": favorite_text, "callback_data": f"favorite:{opportunity_id}"},
                {"text": "Скрыть", "callback_data": f"hide:{opportunity_id}"},
                link_button,
            ]
        ]
    }


def _format_employment_match_message(
    candidate: NotificationCandidate,
    rates: ExchangeRates | None,
) -> str:
    company = escape(candidate.company or "Компания не указана")
    location = escape(_format_location(candidate.location_text))
    employment = escape(_format_employment_type(candidate.employment_type))
    title = _format_linked_title(candidate.title, candidate.source_url)
    lines = [
        f"<b>[{escape(candidate.source_display_name)}] Вакансия: {candidate.score}/100</b>",
        title,
        f"Компания: {company}",
        f"Локация: {location}",
        f"Занятость: {employment}",
    ]
    salary = _format_salary(candidate, rates)
    if salary:
        lines.extend(("<b>Зарплата</b>", *(f"- {escape(value)}" for value in salary)))
    lines.extend(("", "<b>Почему подходит</b>"))
    lines.extend(f"- {escape(reason)}" for reason in candidate.reasons[:3])
    if candidate.concerns:
        lines.extend(("", "<b>На что обратить внимание</b>"))
        lines.extend(f"- {escape(concern)}" for concern in candidate.concerns[:2])
    lines.extend(("", f'<a href="{escape(candidate.source_url, quote=True)}">Открыть вакансию</a>'))
    return "\n".join(lines)


def _format_freelance_match_message(
    candidate: NotificationCandidate,
    rates: ExchangeRates | None,
) -> str:
    contract_type = _format_contract_type(candidate.contract_type)
    title = _format_linked_title(candidate.title, candidate.source_url)
    lines = [
        (f"<b>[{escape(candidate.source_display_name)}] Фриланс-проект: {candidate.score}/100</b>"),
        title,
        f"Заказчик: {escape(candidate.company or 'Не указан')}",
        f"Тип проекта: {escape(contract_type)}",
    ]
    budget = _format_salary(candidate, rates)
    if budget:
        lines.extend(("<b>Бюджет</b>", *(f"- {escape(value)}" for value in budget)))
    bid_count = _bid_count(candidate.raw_data)
    if bid_count is not None:
        lines.append(f"Конкуренция: {bid_count} {_russian_bid_word(bid_count)}")
    employer_status = _employer_status(candidate.raw_data)
    if employer_status is not None:
        lines.append(f"Статус заказчика: {escape(employer_status)}")

    lines.extend(("", "<b>Почему подходит</b>"))
    lines.extend(f"- {escape(reason)}" for reason in candidate.reasons[:3])
    if candidate.concerns:
        lines.extend(("", "<b>На что обратить внимание</b>"))
        lines.extend(f"- {escape(concern)}" for concern in candidate.concerns[:2])
    lines.extend(("", f'<a href="{escape(candidate.source_url, quote=True)}">Открыть проект</a>'))
    return "\n".join(lines)


def _format_linked_title(title: str, source_url: str) -> str:
    return f'<a href="{escape(source_url, quote=True)}"><b>{escape(title)}</b></a>'


def _format_salary(
    candidate: NotificationCandidate,
    rates: ExchangeRates | None,
) -> tuple[str, ...]:
    if candidate.salary_min is None and candidate.salary_max is None:
        return ()
    if rates is None:
        return format_original_range(
            candidate.salary_min,
            candidate.salary_max,
            candidate.salary_currency,
            candidate.salary_period,
        )
    try:
        return format_converted_range(
            candidate.salary_min,
            candidate.salary_max,
            candidate.salary_currency,
            candidate.salary_period,
            rates,
        )
    except CurrencyConversionError:
        return format_original_range(
            candidate.salary_min,
            candidate.salary_max,
            candidate.salary_currency,
            candidate.salary_period,
        )


def _has_published_amount(candidate: NotificationCandidate) -> bool:
    return candidate.salary_min is not None or candidate.salary_max is not None


def _format_contract_type(value: str | None) -> str:
    if value == "fixed":
        return "Фиксированная цена"
    if value == "hourly":
        return "Почасовая оплата"
    return "Не указан"


def _format_employment_type(value: str | None) -> str:
    if not value:
        return "Не указана"
    labels = {
        "full_time": "Полная занятость",
        "part_time": "Частичная занятость",
        "contractor": "Контракт",
        "temporary": "Временная работа",
        "internship": "Стажировка",
        "freelance": "Фриланс",
    }
    values = [item.strip() for item in value.split(",") if item.strip()]
    return ", ".join(labels.get(item, item.replace("_", " ")) for item in values)


def _format_location(value: str | None) -> str:
    if not value:
        return "Удалённо"
    locations = {
        "remote": "Удалённо",
        "remote worldwide": "Удалённо, весь мир",
        "remote europe": "Удалённо, Европа",
    }
    return locations.get(value.casefold(), value)


def _russian_bid_word(value: int) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return "ставка"
    if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return "ставки"
    return "ставок"


def _bid_count(raw_data: dict[str, object]) -> int | None:
    bid_stats = raw_data.get("bid_stats")
    if not isinstance(bid_stats, dict):
        return None
    value = bid_stats.get("bid_count")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _employer_status(raw_data: dict[str, object]) -> str | None:
    owner = raw_data.get("_owner") or raw_data.get("owner_info")
    if not isinstance(owner, dict):
        return None
    parts: list[str] = []
    status = owner.get("status")
    if isinstance(status, dict) and status.get("payment_verified") is True:
        parts.append("платёжные данные подтверждены")
    reputation = owner.get("employer_reputation")
    history = reputation.get("entire_history") if isinstance(reputation, dict) else None
    if isinstance(history, dict):
        rating = history.get("overall")
        reviews = history.get("reviews")
        if isinstance(rating, int | float) and isinstance(reviews, int):
            parts.append(f"рейтинг {rating:g}/5 на основе {reviews} отзывов")
    return ", ".join(parts) or None


def _event_key(rules_version: str, content_hash: str) -> str:
    return f"match:{rules_version}:{content_hash[:16]}"


def _retry_delay(attempts: int) -> timedelta:
    exponent = min(6, max(0, attempts - 1))
    return timedelta(seconds=min(3600, 60 * (2**exponent)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
