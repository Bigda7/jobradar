import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import NotificationDelivery, Opportunity, TelegramOpportunityMessage
from jobradar.domain.enums import DeliveryStatus
from jobradar.ingestion.service import IngestionService
from jobradar.notifications.currency import NbuExchangeRateClient
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import TelegramClient
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration


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
