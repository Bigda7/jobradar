from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    MatchEvaluation,
    NotificationDelivery,
    OpportunityUserState,
    TelegramOpportunityMessage,
)
from jobradar.domain.enums import DeliveryStatus, OpportunityDisposition, OpportunityKind
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.currency import CurrencyConversionError, ExchangeRates
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import (
    NotificationCandidate,
    NotificationService,
    _retry_delay,
    format_match_message,
)
from jobradar.notifications.telegram import (
    InlineKeyboardMarkup,
    TelegramClient,
    TelegramDeliveryError,
)
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


class FreelanceMockSource(MockSource):
    name = "freelance_mock"
    display_name = "Project Board"
    opportunity_kind = OpportunityKind.FREELANCE_PROJECT


class RecordingTelegramClient(TelegramClient):
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reply_markups: list[InlineKeyboardMarkup | None] = []

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.messages.append(text)
        self.reply_markups.append(reply_markup)
        return len(self.messages)


class RecoveringTelegramClient(RecordingTelegramClient):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise TelegramDeliveryError("Simulated Telegram outage.")
        return await super().send_message(text, reply_markup, chat_id)


TEST_RATES = ExchangeRates(
    {
        "USD": Decimal("40"),
        "UAH": Decimal("1"),
        "CZK": Decimal("2"),
    },
    effective_date="22.08.2026",
)


class FixedExchangeRateProvider:
    async def fetch_rates(self) -> ExchangeRates:
        return TEST_RATES


class FailingExchangeRateProvider:
    async def fetch_rates(self) -> ExchangeRates:
        raise CurrencyConversionError("NBU is unavailable")


async def _allow_retry_now(
    session_factory: async_sessionmaker[AsyncSession], delivery_id: int
) -> None:
    async with session_factory() as session, session.begin():
        delivery = await session.get(NotificationDelivery, delivery_id)
        assert delivery is not None
        assert delivery.next_attempt_at is not None
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)


@pytest.mark.parametrize(
    ("attempts", "delay_seconds"),
    ((1, 60), (2, 120), (3, 240), (7, 3600), (100, 3600)),
)
def test_telegram_retry_delay_is_bounded(attempts: int, delay_seconds: int) -> None:
    assert _retry_delay(attempts) == timedelta(seconds=delay_seconds)


@pytest.mark.asyncio
async def test_telegram_client_uses_bot_api_json_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient(
            bot_token="test-token",
            chat_id=123,
            client=http_client,
            api_base_url="https://telegram.test",
        )
        bot = await client.get_me()
        message_id = await client.send_message("<b>Test</b>")

    assert bot["is_bot"] is True
    assert message_id == 42
    assert len(requests) == 2
    assert requests[1].method == "POST"
    assert b'"parse_mode":"HTML"' in requests[1].content
    assert b'"chat_id":123' in requests[1].content


@pytest.mark.asyncio
async def test_prague_rules_recalculation_does_not_resend_historical_matches(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    previous = replace(BOHDAN_PROFILE, rules_version="bohdan-multi-source-v14-live-exchange-rates")
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    matching = MatchingService(sqlite_session_factory)
    await matching.evaluate(previous)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    first = await service.dispatch(
        previous,
        minimum_score=previous.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    recalculated = await matching.evaluate(BOHDAN_PROFILE)
    second = await service.dispatch(
        BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert recalculated.evaluated > 0
    assert first.sent == 2
    assert second.sent == 0 and second.failed == 0 and second.skipped_duplicate == 2
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_notification_delivery_is_idempotent(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert first.sent == 2
    assert second.sent == 0
    assert second.skipped_duplicate == 2
    assert len(client.messages) == 2
    assert all(markup is not None for markup in client.reply_markups)
    assert all(len(markup["inline_keyboard"][0]) == 3 for markup in client.reply_markups if markup)
    assert all("[Mock Source] Вакансия" in message for message in client.messages)
    assert any("Локация: Удалённо, Европа" in message for message in client.messages)
    assert any("- USD: 1,200-1,800 / месяц" in message for message in client.messages)
    assert any("- UAH: 48,000-72,000 / месяц" in message for message in client.messages)
    assert any("- CZK: 24,000-36,000 / месяц" in message for message in client.messages)
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 2
        linked_messages = await session.scalars(select(TelegramOpportunityMessage.delivery_id))
        assert set(linked_messages.all()) == set(
            (await session.scalars(select(NotificationDelivery.id))).all()
        )


@pytest.mark.asyncio
async def test_rate_rescore_does_not_repeat_delivered_historical_notifications(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    listings = deepcopy(DEFAULT_LISTINGS)
    listings[0]["salary_min"] = "30000"
    listings[0]["salary_max"] = "35000"
    listings[0]["salary_currency"] = "CZK"
    await IngestionService(sqlite_session_factory).run_source(MockSource(listings))
    await MatchingService(sqlite_session_factory, FixedExchangeRateProvider()).evaluate(
        BOHDAN_PROFILE
    )
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert first.sent == 2

    class UpdatedRatesProvider:
        async def fetch_rates(self) -> ExchangeRates:
            return ExchangeRates({"USD": Decimal("41"), "CZK": Decimal("2")})

    rescored = await MatchingService(sqlite_session_factory, UpdatedRatesProvider()).evaluate(
        BOHDAN_PROFILE
    )
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert rescored.evaluated == 1
    assert second.sent == 0
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_pending_delivery_is_retried_automatically_when_unconfirmed(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    candidates = await service.load_candidates(
        BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold
    )
    pending = candidates[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    await _allow_retry_now(sqlite_session_factory, pending_id)
    third = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert first.sent == 1
    assert first.retry_deferred == 1
    assert second.sent == 0
    assert second.retry_deferred == 1
    assert third.sent == 1
    assert len(client.messages) == 2
    assert sum(pending.title in message for message in client.messages) == 1
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 2


@pytest.mark.asyncio
async def test_pending_delivery_with_recorded_message_is_confirmed_without_resending(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    candidates = await service.load_candidates(
        BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold
    )
    pending = candidates[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None
    await service._message_registry.record(pending.opportunity_id, 42, delivery_id=pending_id)

    result = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert result.sent == 1
    assert len(client.messages) == 1
    assert pending.title not in client.messages[0]
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 1
        assert delivery.sent_at is not None


@pytest.mark.asyncio
async def test_pending_delivery_is_not_confirmed_by_manual_message(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    candidates = await service.load_candidates(
        BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold
    )
    pending = candidates[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None
    await service._message_registry.record(pending.opportunity_id, 42)

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    await _allow_retry_now(sqlite_session_factory, pending_id)
    result = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert first.sent == 1
    assert first.retry_deferred == 1
    assert result.sent == 1
    assert sum(pending.title in message for message in client.messages) == 1
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 2
        manual_message = await session.scalar(
            select(TelegramOpportunityMessage).where(
                TelegramOpportunityMessage.telegram_message_id == 42
            )
        )
        assert manual_message is not None
        assert manual_message.delivery_id is None


@pytest.mark.asyncio
async def test_pending_delivery_with_updated_content_is_retried_once(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    candidates = await service.load_candidates(
        BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold
    )
    pending = candidates[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None

    updated_listings = deepcopy(DEFAULT_LISTINGS)
    for listing in updated_listings:
        listing["description"] += " Updated requirements."
    await IngestionService(sqlite_session_factory).run_source(MockSource(updated_listings))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    await _allow_retry_now(sqlite_session_factory, pending_id)
    result = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert first.sent == 0
    assert first.retry_deferred == 1
    assert result.sent == 1
    assert len(client.messages) == 1
    assert sum(pending.title in message for message in client.messages) == 1
    async with sqlite_session_factory() as session:
        deliveries = list(
            await session.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.opportunity_id == pending.opportunity_id
                )
            )
        )
    assert len(deliveries) == 1
    assert deliveries[0].status == DeliveryStatus.SENT.value
    assert deliveries[0].attempts == 2


@pytest.mark.asyncio
async def test_pending_delivery_waits_while_paused_and_recovers_after_resume(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    pending = (
        await service.load_candidates(BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold)
    )[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None
    preferences = NotificationPreferenceService(sqlite_session_factory)
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)

    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert paused.sent == 0
    assert client.messages == []
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.PENDING.value

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    resumed = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    await _allow_retry_now(sqlite_session_factory, pending_id)
    retried = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )

    assert resumed.sent == 1
    assert resumed.retry_deferred == 1
    assert retried.sent == 1
    assert len(client.messages) == 2
    assert sum(pending.title in message for message in client.messages) == 1


@pytest.mark.asyncio
async def test_pending_delivery_retries_after_three_interrupted_attempts(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    pending = (
        await service.load_candidates(BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold)
    )[0]
    event_key = f"match:{BOHDAN_PROFILE.rules_version}:{pending.content_hash[:16]}"
    pending_id = await service._claim_delivery(pending.opportunity_id, BOHDAN_PROFILE, event_key)
    assert pending_id is not None
    async with sqlite_session_factory() as session, session.begin():
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        delivery.attempts = 2
    updated_listings = deepcopy(DEFAULT_LISTINGS)
    for listing in updated_listings:
        listing["description"] += " Updated requirements."
    await IngestionService(sqlite_session_factory).run_source(MockSource(updated_listings))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)

    result = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert result.sent == 0
    assert result.retry_deferred == 1
    assert client.messages == []
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.FAILED.value
        assert delivery.attempts == 3
        assert delivery.next_attempt_at is not None

    await _allow_retry_now(sqlite_session_factory, pending_id)
    retried = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert retried.sent == 1
    assert len(client.messages) == 1
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, pending_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 4
        assert delivery.next_attempt_at is None


@pytest.mark.asyncio
async def test_telegram_outage_retries_until_delivery_succeeds(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource(DEFAULT_LISTINGS[:1]))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecoveringTelegramClient(failures=4)
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    candidates = await service.load_candidates(
        BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold
    )
    assert len(candidates) == 1

    for attempt in range(1, 5):
        result = await service.dispatch(
            profile=BOHDAN_PROFILE,
            minimum_score=BOHDAN_PROFILE.notification_threshold,
            max_messages=5,
            minimum_first_seen_at=None,
        )
        assert result.failed == 1
        assert result.sent == 0
        assert client.attempts == attempt
        async with sqlite_session_factory() as session:
            delivery = await session.scalar(select(NotificationDelivery))
            assert delivery is not None
            assert delivery.status == DeliveryStatus.FAILED.value
            assert delivery.attempts == attempt
            assert delivery.next_attempt_at is not None
            delivery_id = delivery.id

        deferred = await service.dispatch(
            profile=BOHDAN_PROFILE,
            minimum_score=BOHDAN_PROFILE.notification_threshold,
            max_messages=5,
            minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        assert deferred.retry_deferred == 1
        assert client.attempts == attempt
        await _allow_retry_now(sqlite_session_factory, delivery_id)

    recovered = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    duplicate = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert recovered.sent == 1
    assert duplicate.sent == 0
    assert client.attempts == 5
    assert len(client.messages) == 1
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 5
        assert delivery.next_attempt_at is None


@pytest.mark.asyncio
async def test_stored_delivery_survives_inactive_listing_and_respects_pause(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource(DEFAULT_LISTINGS[:1]))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecoveringTelegramClient(failures=1)
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert first.failed == 1
    async with sqlite_session_factory() as session, session.begin():
        delivery = await session.scalar(select(NotificationDelivery))
        listing = await session.scalar(select(Listing))
        assert delivery is not None
        assert listing is not None
        assert delivery.message_text is not None
        assert delivery.source_url == listing.source_url
        delivery_id = delivery.id
        listing.is_active = False

    assert (
        await service.load_candidates(BOHDAN_PROFILE, BOHDAN_PROFILE.notification_threshold) == []
    )
    await _allow_retry_now(sqlite_session_factory, delivery_id)
    preferences = NotificationPreferenceService(sqlite_session_factory)
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    paused = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=5)
    assert paused.sent == 0
    assert client.attempts == 1

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    retried = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=5)
    duplicate = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=5)
    assert retried.sent == 1
    assert duplicate.sent == 0
    assert client.attempts == 2
    assert len(client.messages) == 1
    async with sqlite_session_factory() as session:
        delivery = await session.get(NotificationDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.next_attempt_at is None


@pytest.mark.asyncio
async def test_failed_delivery_survives_reevaluation_while_paused(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource(DEFAULT_LISTINGS[:1]))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecoveringTelegramClient(failures=1)
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    preferences = NotificationPreferenceService(sqlite_session_factory)

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    assert first.failed == 1
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    async with sqlite_session_factory() as session, session.begin():
        evaluation = await session.scalar(select(MatchEvaluation))
        assert evaluation is not None
        evaluation.evaluated_at = datetime.now(UTC) + timedelta(seconds=1)

    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    assert paused.sent == 0
    assert paused.skipped_paused == 0
    async with sqlite_session_factory() as session:
        delivery = await session.scalar(select(NotificationDelivery))
        assert delivery is not None
        assert delivery.status == DeliveryStatus.FAILED.value
        delivery_id = delivery.id

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    await _allow_retry_now(sqlite_session_factory, delivery_id)
    retried = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=1)
    assert retried.sent == 1
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_notification_limit_preserves_queued_matches_for_next_cycle(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_cycle_started_at = datetime.now(UTC) - timedelta(minutes=1)
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=first_cycle_started_at,
    )
    async with sqlite_session_factory() as session:
        first_statuses = list(await session.scalars(select(NotificationDelivery.status)))

    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    third = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert first.sent == 1
    assert sorted(first_statuses) == sorted(
        (DeliveryStatus.QUEUED.value, DeliveryStatus.SENT.value)
    )
    assert second.sent == 1
    assert third.sent == 0
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_queued_match_is_sent_after_listing_disappears(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    assert first.sent == 1
    async with sqlite_session_factory() as session, session.begin():
        queued = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.status == DeliveryStatus.QUEUED.value
            )
        )
        assert queued is not None
        assert queued.message_text is not None
        assert queued.source_url is not None
        queued_id = queued.id
        listings = list(
            await session.scalars(
                select(Listing).where(Listing.opportunity_id == queued.opportunity_id)
            )
        )
        assert listings
        for listing in listings:
            listing.is_active = False

    retried = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=1)
    assert retried.sent == 1
    assert len(client.messages) == 2
    async with sqlite_session_factory() as session:
        queued = await session.get(NotificationDelivery, queued_id)
        assert queued is not None
        assert queued.status == DeliveryStatus.SENT.value


@pytest.mark.asyncio
async def test_pausing_discards_queued_match_after_listing_disappears(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    preferences = NotificationPreferenceService(sqlite_session_factory)

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    assert first.sent == 1
    async with sqlite_session_factory() as session, session.begin():
        queued = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.status == DeliveryStatus.QUEUED.value
            )
        )
        assert queued is not None
        queued_id = queued.id
        listings = list(
            await session.scalars(
                select(Listing).where(Listing.opportunity_id == queued.opportunity_id)
            )
        )
        for listing in listings:
            listing.is_active = False

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    retried = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=1)

    assert paused.skipped_paused == 1
    assert retried.sent == 0
    assert len(client.messages) == 1
    async with sqlite_session_factory() as session:
        queued = await session.get(NotificationDelivery, queued_id)
        assert queued is not None
        assert queued.status == DeliveryStatus.SKIPPED_PAUSED.value


@pytest.mark.asyncio
async def test_queued_match_respects_explicit_hidden_state(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    assert first.sent == 1
    async with sqlite_session_factory() as session, session.begin():
        queued = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.status == DeliveryStatus.QUEUED.value
            )
        )
        assert queued is not None
        session.add(
            OpportunityUserState(
                opportunity_id=queued.opportunity_id,
                disposition=OpportunityDisposition.HIDDEN.value,
            )
        )

    hidden = await service.retry_due(BOHDAN_PROFILE.profile_id, max_messages=1)
    assert hidden.sent == 0
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_queued_match_survives_content_update_before_next_cycle(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    changed_listings = deepcopy(DEFAULT_LISTINGS)
    for listing in changed_listings:
        listing["description"] += " Updated requirements."
    await IngestionService(sqlite_session_factory).run_source(MockSource(changed_listings))
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)

    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert first.sent == 1
    assert second.sent == 1
    assert len(client.messages) == 2
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 2


@pytest.mark.asyncio
async def test_pausing_discards_queued_matches_without_sending_after_resume(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    preferences = NotificationPreferenceService(sqlite_session_factory)

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    resumed = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )

    assert first.sent == 1
    assert paused.skipped_paused == 1
    assert resumed.sent == 0
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_notification_delivery_falls_back_to_original_currency(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()

    result = await NotificationService(
        sqlite_session_factory,
        client,
        FailingExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert result.sent == 2
    assert result.failed == 0
    assert any("- USD: 1,200-1,800 / месяц" in message for message in client.messages)
    assert all("- UAH:" not in message for message in client.messages)


@pytest.mark.asyncio
async def test_manual_candidate_lists_exclude_inactive_opportunities(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    async with sqlite_session_factory() as session, session.begin():
        listing = await session.scalar(select(Listing).where(Listing.external_id == "mock-002"))
        assert listing is not None
        listing.is_active = False
        listing.archive_reason = "expired"
    service = NotificationService(
        sqlite_session_factory,
        RecordingTelegramClient(),
        FixedExchangeRateProvider(),
    )

    candidates = await service.load_candidates(
        BOHDAN_PROFILE,
        BOHDAN_PROFILE.notification_threshold,
    )

    assert [candidate.title for candidate in candidates] == ["Junior Full-Stack Developer"]


@pytest.mark.asyncio
async def test_updated_listing_does_not_send_the_same_opportunity_again(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert first.sent == 2

    changed_listings = deepcopy(DEFAULT_LISTINGS)
    changed_listings[0]["description"] += " Updated requirements."
    await ingestion.run_source(MockSource(changed_listings))
    matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert matching.evaluated == 1
    assert second.sent == 0
    assert second.skipped_duplicate == 2
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_notification_delivery_skips_historical_matches(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()

    result = await NotificationService(
        sqlite_session_factory,
        client,
        FixedExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert result.sent == 0
    assert result.skipped_historical == 2
    assert client.messages == []
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 0


@pytest.mark.asyncio
async def test_paused_notifications_are_recorded_and_not_sent_after_resume(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    preferences = NotificationPreferenceService(sqlite_session_factory)
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )

    assert paused.considered == 2
    assert paused.skipped_paused == 2
    assert paused.sent == 0
    assert client.messages == []
    async with sqlite_session_factory() as session:
        statuses = list(await session.scalars(select(NotificationDelivery.status)))
    assert statuses == [
        DeliveryStatus.SKIPPED_PAUSED.value,
        DeliveryStatus.SKIPPED_PAUSED.value,
    ]

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    resumed = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert resumed.sent == 0
    assert resumed.skipped_duplicate == 2
    assert client.messages == []


def test_freelance_notification_uses_project_specific_template() -> None:
    candidate = NotificationCandidate(
        opportunity_id=101,
        kind=OpportunityKind.FREELANCE_PROJECT,
        title="Django API integration",
        company="Verified Employer",
        location_text="Remote",
        employment_type=None,
        contract_type="fixed",
        salary_min=Decimal("300"),
        salary_max=Decimal("600"),
        salary_currency="USD",
        salary_period="project",
        first_seen_at=datetime.now(UTC),
        source_display_name="Project Board",
        source_url="https://projects.example.test/django-api-integration",
        content_hash="a" * 64,
        score=82,
        reasons=(
            "Совпавшие навыки: Python, Django, REST APIs.",
            "Фиксированный бюджет достигает предпочтительного диапазона: USD 300-600.",
        ),
        concerns=("Высокая конкуренция: 65 ставок.",),
        raw_data={
            "bid_stats": {"bid_count": 65},
            "_owner": {
                "status": {"payment_verified": True},
                "employer_reputation": {"entire_history": {"overall": 4.8, "reviews": 24}},
            },
        },
    )

    message = format_match_message(candidate, TEST_RATES)

    assert "[Project Board] Фриланс-проект: 82/100" in message
    assert "Тип проекта: Фиксированная цена" in message
    assert "<b>Бюджет</b>" in message
    assert "- USD: 300-600 / проект" in message
    assert "- UAH: 12,000-24,000 / проект" in message
    assert "- CZK: 6,000-12,000 / проект" in message
    assert "Конкуренция: 65 ставок" in message
    assert "Статус заказчика: платёжные данные подтверждены" in message
    assert "рейтинг 4.8/5 на основе 24 отзывов" in message
    assert ">Открыть проект</a>" in message


@pytest.mark.asyncio
async def test_freelance_match_is_delivered_with_freelance_template(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    listing = {
        "id": "project-9001",
        "url": "https://projects.example.test/django-api-integration",
        "title": "Small Django REST API webhook integration",
        "company": "Verified Employer",
        "description": "Build a small React dashboard and PostgreSQL webhook.",
        "location": "Remote",
        "work_mode": "remote",
        "employment_type": None,
        "contract_type": "fixed",
        "salary_min": "300",
        "salary_max": "600",
        "salary_currency": "USD",
        "salary_period": "project",
        "published_at": "2026-08-22T09:00:00+00:00",
        "bid_stats": {"bid_count": 5},
        "_owner": {"status": {"payment_verified": True}},
    }
    await IngestionService(sqlite_session_factory).run_source(FreelanceMockSource((listing,)))

    matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    delivery = await NotificationService(
        sqlite_session_factory,
        client,
        FixedExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=3,
        minimum_first_seen_at=None,
    )

    assert matching.evaluated == 1
    assert delivery.sent == 1
    assert len(client.messages) == 1
    assert "[Project Board] Фриланс-проект" in client.messages[0]
    assert "Конкуренция: 5 ставок" in client.messages[0]
    assert ">Открыть проект</a>" in client.messages[0]
