from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from typing import cast

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    NotificationDelivery,
    Opportunity,
    OpportunityUserState,
    Source,
)
from jobradar.domain.enums import DeliveryStatus, OpportunityDisposition, OpportunityKind
from jobradar.ingestion.canonical import canonical_listing_order
from jobradar.matching.profile import SearchProfile
from jobradar.notifications.currency import (
    CurrencyConversionError,
    ExchangeRateProvider,
    ExchangeRates,
    format_converted_range,
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


class NotificationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_client: TelegramClient,
        exchange_rate_provider: ExchangeRateProvider,
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
                if (
                    pause_state.paused_at is not None
                    and candidate.evaluated_at is not None
                    and _as_utc(candidate.evaluated_at) < _as_utc(pause_state.paused_at)
                ):
                    summary.skipped_historical += 1
                    continue
                summary.considered += 1
                event_key = _event_key(profile.rules_version, candidate.content_hash)
                if await self._mark_skipped_paused(
                    candidate.opportunity_id,
                    profile,
                    event_key,
                ):
                    summary.skipped_paused += 1
                else:
                    summary.skipped_duplicate += 1
            return summary

        rates: ExchangeRates | None = None
        if any(_has_published_amount(candidate) for candidate in candidates):
            try:
                rates = await self._exchange_rate_provider.fetch_rates()
            except CurrencyConversionError as error:
                failed_count = min(len(candidates), max_messages)
                summary.considered = failed_count
                summary.failed = failed_count
                logger.warning(
                    "currency_conversion_unavailable",
                    candidates=failed_count,
                    error=str(error),
                )
                return summary
        for candidate in candidates:
            if summary.sent >= max_messages:
                break
            summary.considered += 1
            event_key = _event_key(profile.rules_version, candidate.content_hash)
            delivery = await self._get_delivery(candidate.opportunity_id, profile, event_key)
            is_retry = delivery is not None and delivery.status == DeliveryStatus.FAILED.value
            if (
                minimum_first_seen_at is not None
                and _as_utc(candidate.first_seen_at) < _as_utc(minimum_first_seen_at)
                and not is_retry
            ):
                summary.skipped_historical += 1
                continue

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

            delivery_id = await self._claim_delivery(
                candidate.opportunity_id,
                profile,
                event_key,
            )
            if delivery_id is None:
                summary.skipped_duplicate += 1
                continue

            try:
                message_id = await self._telegram_client.send_message(
                    message,
                    reply_markup=opportunity_keyboard(
                        candidate.opportunity_id, candidate.source_url
                    ),
                )
                await self._message_registry.record(candidate.opportunity_id, message_id)
            except TelegramDeliveryError as error:
                await self._finish_delivery(delivery_id, sent=False, error=str(error))
                summary.failed += 1
                logger.warning(
                    "telegram_delivery_failed",
                    opportunity_id=candidate.opportunity_id,
                    error=str(error),
                )
            else:
                await self._finish_delivery(delivery_id, sent=True, error=None)
                summary.sent += 1
        return summary

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
                        )
                        .order_by(*canonical_listing_order())
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
            return cast(
                NotificationDelivery | None,
                await session.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.opportunity_id == opportunity_id,
                        NotificationDelivery.profile_id == profile.profile_id,
                        NotificationDelivery.channel == "telegram",
                        NotificationDelivery.event_key == event_key,
                    )
                ),
            )

    async def _claim_delivery(
        self,
        opportunity_id: int,
        profile: SearchProfile,
        event_key: str,
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
                handled_delivery_id = await session.scalar(
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
                if handled_delivery_id is not None:
                    return None
                delivery = NotificationDelivery(
                    opportunity_id=opportunity_id,
                    profile_id=profile.profile_id,
                    channel="telegram",
                    event_key=event_key,
                    status=DeliveryStatus.PENDING.value,
                )
                session.add(delivery)
                await session.flush()
                return delivery.id
            if delivery.status != DeliveryStatus.FAILED.value or delivery.attempts >= 3:
                return None
            delivery.status = DeliveryStatus.PENDING.value
            delivery.last_error = None
            return delivery.id

    async def _finish_delivery(
        self,
        delivery_id: int,
        sent: bool,
        error: str | None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            delivery = await session.get(NotificationDelivery, delivery_id)
            if delivery is None:
                raise RuntimeError("Notification delivery disappeared before completion.")
            delivery.attempts += 1
            delivery.status = DeliveryStatus.SENT.value if sent else DeliveryStatus.FAILED.value
            delivery.last_error = error[:2000] if error else None
            delivery.sent_at = datetime.now(UTC) if sent else None

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
                handled_delivery_id = await session.scalar(
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
                DeliveryStatus.SENT.value,
                DeliveryStatus.SKIPPED_PAUSED.value,
            }:
                return False
            delivery.status = DeliveryStatus.SKIPPED_PAUSED.value
            delivery.last_error = None
            delivery.sent_at = None
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
                        "text": "Восстановить \U0001f504",
                        "callback_data": f"restore:{opportunity_id}",
                    },
                    link_button,
                ]
            ]
        }
    favorite_text = "В избранном \u2b50" if is_favorite else "В избранное \u2b50"
    return {
        "inline_keyboard": [
            [
                {"text": favorite_text, "callback_data": f"favorite:{opportunity_id}"},
                {"text": "Скрыть \u274c", "callback_data": f"hide:{opportunity_id}"},
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
        raise CurrencyConversionError("Exchange rates are required for a published amount.")
    return format_converted_range(
        candidate.salary_min,
        candidate.salary_max,
        candidate.salary_currency,
        candidate.salary_period,
        rates,
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
