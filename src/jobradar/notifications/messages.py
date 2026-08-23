from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import TelegramOpportunityMessage


class TelegramMessageRegistry:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, opportunity_id: int, telegram_message_id: int) -> None:
        async with self._session_factory() as session, session.begin():
            message = await session.scalar(
                select(TelegramOpportunityMessage).where(
                    TelegramOpportunityMessage.telegram_message_id == telegram_message_id
                )
            )
            if message is None:
                session.add(
                    TelegramOpportunityMessage(
                        opportunity_id=opportunity_id,
                        telegram_message_id=telegram_message_id,
                    )
                )
                return
            message.opportunity_id = opportunity_id
            message.deleted_at = None

    async def active_message_ids(self) -> list[int]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(TelegramOpportunityMessage.telegram_message_id)
                .where(TelegramOpportunityMessage.deleted_at.is_(None))
                .order_by(TelegramOpportunityMessage.telegram_message_id.desc())
            )
            return list(values.all())

    async def mark_deleted(self, telegram_message_ids: Iterable[int]) -> int:
        message_ids = tuple(telegram_message_ids)
        if not message_ids:
            return 0
        async with self._session_factory() as session, session.begin():
            active_ids = tuple(
                (
                    await session.scalars(
                        select(TelegramOpportunityMessage.telegram_message_id).where(
                            TelegramOpportunityMessage.telegram_message_id.in_(message_ids),
                            TelegramOpportunityMessage.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            if not active_ids:
                return 0
            await session.execute(
                update(TelegramOpportunityMessage)
                .where(
                    TelegramOpportunityMessage.telegram_message_id.in_(active_ids),
                )
                .values(deleted_at=datetime.now(UTC))
            )
            return len(active_ids)
