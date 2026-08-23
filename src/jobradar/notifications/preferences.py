from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import NotificationPreference


@dataclass(frozen=True, slots=True)
class PauseChange:
    is_paused: bool
    changed: bool


@dataclass(frozen=True, slots=True)
class NotificationPauseState:
    is_paused: bool
    paused_at: datetime | None


class NotificationPreferenceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_paused(self, profile_id: str, channel: str) -> bool:
        return (await self.get_state(profile_id, channel)).is_paused

    async def get_state(self, profile_id: str, channel: str) -> NotificationPauseState:
        async with self._session_factory() as session:
            preference = await session.get(NotificationPreference, (profile_id, channel))
            if preference is None:
                return NotificationPauseState(is_paused=False, paused_at=None)
            return NotificationPauseState(
                is_paused=preference.is_paused,
                paused_at=preference.paused_at,
            )

    async def set_paused(self, profile_id: str, channel: str, paused: bool) -> PauseChange:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            preference = await session.get(NotificationPreference, (profile_id, channel))
            if preference is None:
                preference = NotificationPreference(
                    profile_id=profile_id,
                    channel=channel,
                    is_paused=paused,
                    paused_at=now if paused else None,
                    resumed_at=now if not paused else None,
                )
                session.add(preference)
                return PauseChange(is_paused=paused, changed=paused)

            changed = preference.is_paused != paused
            preference.is_paused = paused
            if changed and paused:
                preference.paused_at = now
            elif changed:
                preference.resumed_at = now
            return PauseChange(is_paused=paused, changed=changed)
