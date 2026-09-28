from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import NotificationScanCursor


class NotificationScanCursorService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def begin_cycle(self, profile_id: str, channel: str, started_at: datetime) -> datetime:
        async with self._session_factory() as session, session.begin():
            cursor = await session.get(NotificationScanCursor, (profile_id, channel))
            if cursor is None:
                cursor = NotificationScanCursor(
                    profile_id=profile_id,
                    channel=channel,
                    minimum_first_seen_at=started_at,
                )
                session.add(cursor)
            value = cursor.minimum_first_seen_at
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def complete_cycle(self, profile_id: str, channel: str, started_at: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            cursor = await session.get(NotificationScanCursor, (profile_id, channel))
            if cursor is None:
                raise RuntimeError("Notification scan cursor is missing.")
            cursor.minimum_first_seen_at = started_at
