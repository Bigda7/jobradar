from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import (
    Listing,
    NotificationDelivery,
    Opportunity,
    TelegramOpportunityMessage,
)
from jobradar.domain.enums import DeliveryStatus
from jobradar.ingestion.service import IngestionService
from jobradar.notifications.currency import NbuExchangeRateClient
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import InlineKeyboardMarkup, TelegramClient
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration


class RecordingTelegramClient(TelegramClient):
    def __init__(self) -> None:
        super().__init__(bot_token="test-token", chat_id=123)
        self.messages: list[str] = []

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.messages.append(text)
        return len(self.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_to_delivery", (True, False))
async def test_postgres_only_recovers_pending_delivery_from_its_own_message(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    linked_to_delivery: bool,
) -> None:
    await IngestionService(postgres_session_factory).run_source(MockSource())
    async with postgres_session_factory() as session, session.begin():
        opportunity_id = await session.scalar(select(Opportunity.id).limit(1))
        assert opportunity_id is not None
        delivery = NotificationDelivery(
            opportunity_id=opportunity_id,
            profile_id="test-profile",
            channel="telegram",
            event_key="test-event",
            status=DeliveryStatus.PENDING.value,
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id
        session.add(
            TelegramOpportunityMessage(
                opportunity_id=opportunity_id,
                telegram_message_id=42,
                delivery_id=delivery_id if linked_to_delivery else None,
            )
        )

    service = NotificationService(
        postgres_session_factory,
        TelegramClient(bot_token="test-token", chat_id=123),
        NbuExchangeRateClient(),
    )
    await service._recover_pending_deliveries()

    async with postgres_session_factory() as session:
        recovered = await session.get(NotificationDelivery, delivery_id)
        assert recovered is not None
        assert recovered.status == (
            DeliveryStatus.SENT.value if linked_to_delivery else DeliveryStatus.FAILED.value
        )
        assert recovered.attempts == 1
        assert (recovered.sent_at is not None) is linked_to_delivery
        assert (recovered.next_attempt_at is None) is linked_to_delivery


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (DeliveryStatus.FAILED, DeliveryStatus.QUEUED))
async def test_postgres_retries_stored_message_without_active_listing(
    postgres_session_factory: async_sessionmaker[AsyncSession],
    status: DeliveryStatus,
) -> None:
    await IngestionService(postgres_session_factory).run_source(MockSource())
    async with postgres_session_factory() as session, session.begin():
        opportunity_id = await session.scalar(select(Opportunity.id).limit(1))
        assert opportunity_id is not None
        delivery = NotificationDelivery(
            opportunity_id=opportunity_id,
            profile_id="test-profile",
            channel="telegram",
            event_key="stored-event",
            status=status.value,
            attempts=3 if status == DeliveryStatus.FAILED else 0,
            next_attempt_at=(
                datetime.now(UTC) - timedelta(minutes=1)
                if status == DeliveryStatus.FAILED
                else None
            ),
            message_text="Stored message",
            source_url="https://example.invalid/job",
        )
        session.add(delivery)
        await session.flush()
        delivery_id = delivery.id

    async with postgres_session_factory() as session, session.begin():
        listing = await session.scalar(select(Listing).limit(1))
        assert listing is not None
        listing.is_active = False

    client = RecordingTelegramClient()
    service = NotificationService(postgres_session_factory, client)
    first = await service.retry_due("test-profile", max_messages=1)
    second = await service.retry_due("test-profile", max_messages=1)

    assert first.sent == 1
    assert second.sent == 0
    assert client.messages == ["Stored message"]
    async with postgres_session_factory() as session:
        recovered = await session.get(NotificationDelivery, delivery_id)
        assert recovered is not None
        assert recovered.status == DeliveryStatus.SENT.value
        assert recovered.attempts == (4 if status == DeliveryStatus.FAILED else 1)
        assert recovered.next_attempt_at is None
