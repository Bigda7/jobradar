"""Persist request reservations before network access, including failed attempts."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source
from jobradar.sources.request_budget import reserve_timestamp


class DatabaseRequestBudget:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], source_id: int) -> None:
        self._sessions = sessions
        self._source_id = source_id

    async def reserve(self, category: str) -> bool:
        async with self._sessions() as session, session.begin():
            source = await session.scalar(
                select(Source).where(Source.id == self._source_id).with_for_update()
            )
            if source is None:
                raise RuntimeError("Request budget source disappeared")
            state, allowed = reserve_timestamp(
                source.request_budget, category, datetime.now(UTC).timestamp()
            )
            source.request_budget = state
            return allowed
