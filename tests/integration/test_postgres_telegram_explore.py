from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import MatchEvaluation, Opportunity
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import InlineKeyboardMarkup, TelegramClient
from jobradar.sources.mock import MockSource

pytestmark = pytest.mark.integration


class RecordingClient(TelegramClient):
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.messages.append(text)
        return len(self.messages)


@pytest.mark.parametrize("score", [34, 35, 54, 55])
async def test_postgres_explore_cutoff_deduplication_and_main_threshold(
    postgres_session_factory: async_sessionmaker[AsyncSession], score: int
) -> None:
    activation = datetime.now(UTC)
    await IngestionService(postgres_session_factory).run_source(MockSource())
    await MatchingService(postgres_session_factory).evaluate(BOHDAN_PROFILE)
    async with postgres_session_factory() as session, session.begin():
        opportunities = (await session.scalars(select(Opportunity).order_by(Opportunity.id))).all()
        opportunities[0].first_seen_at = activation - timedelta(seconds=1)
        opportunities[1].first_seen_at = activation
        for opportunity in opportunities:
            opportunity.salary_min = None
            opportunity.salary_max = None
        for evaluation in (await session.scalars(select(MatchEvaluation))).all():
            evaluation.score = score
    client = RecordingClient()
    service = NotificationService(postgres_session_factory, client)
    ordinary = await service.load_candidates(BOHDAN_PROFILE, 55)
    enabled = await service.load_candidates(BOHDAN_PROFILE, 55, explore_since=activation)
    assert len(ordinary) == (2 if score >= 55 else 0)
    expected = 2 if score >= 55 else 1 if score >= 35 else 0
    assert len(enabled) == expected
    first = await service.dispatch(BOHDAN_PROFILE, 55, 5, None, explore_since=activation)
    second = await service.dispatch(BOHDAN_PROFILE, 55, 5, None, explore_since=activation)
    assert first.sent == expected
    assert second.sent == 0
    assert len(client.messages) == expected
    assert all(message.startswith("<b>Explore:") for message in client.messages) is (
        35 <= score <= 54 or expected == 0
    )
