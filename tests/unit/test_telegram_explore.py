from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.config import Settings
from jobradar.db.models import MatchEvaluation, NotificationDelivery, Opportunity
from jobradar.domain.enums import DeliveryStatus
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import (
    InlineKeyboardMarkup,
    TelegramClient,
    TelegramDeliveryError,
)
from jobradar.sources.mock import MockSource


class RecordingClient(TelegramClient):
    def __init__(self, *, fail_once: bool = False) -> None:
        self.messages: list[str] = []
        self.fail_once = fail_once

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        if self.fail_once:
            self.fail_once = False
            raise TelegramDeliveryError("Synthetic temporary outage")
        self.messages.append(text)
        return len(self.messages)


def test_explore_activation_is_optional_and_keeps_main_matching_threshold() -> None:
    settings = Settings(_env_file=None)
    assert settings.telegram_explore_since is None
    assert settings.matching_min_score == 55
    assert BOHDAN_PROFILE.notification_threshold == 55
    assert Settings(_env_file=None, telegram_explore_since="").telegram_explore_since is None
    assert Settings(_env_file=None, telegram_explore_since="  ").telegram_explore_since is None


@pytest.mark.parametrize("value", ["2026-10-18T00:00:00", "not-a-timestamp"])
def test_explore_activation_requires_a_valid_timezone(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_explore_since=value)


def test_explore_activation_normalizes_to_utc() -> None:
    settings = Settings(_env_file=None, telegram_explore_since="2026-10-18T00:00:00+02:00")
    assert settings.telegram_explore_since == datetime(2026, 10, 17, 22, tzinfo=UTC)
    assert settings.matching_min_score == 55


async def seed_candidate(
    factory: async_sessionmaker[AsyncSession], *, score: int, first_seen_at: datetime
) -> None:
    await IngestionService(factory).run_source(MockSource())
    await MatchingService(factory).evaluate(BOHDAN_PROFILE)
    async with factory() as session, session.begin():
        for opportunity in (await session.scalars(select(Opportunity))).all():
            opportunity.first_seen_at = first_seen_at
        for evaluation in (await session.scalars(select(MatchEvaluation))).all():
            evaluation.score = score
            evaluation.concerns = ["Synthetic uncertainty to review before applying."]
        for opportunity in (await session.scalars(select(Opportunity))).all():
            opportunity.salary_min = None
            opportunity.salary_max = None


@pytest.mark.parametrize("score", [34, 35, 54, 55])
@pytest.mark.parametrize("offset", [-1, 0, 1])
async def test_explore_score_and_first_seen_activation_boundaries(
    sqlite_session_factory: async_sessionmaker[AsyncSession], score: int, offset: int
) -> None:
    activation = datetime.now(UTC)
    first_seen = activation + timedelta(microseconds=offset)
    await seed_candidate(sqlite_session_factory, score=score, first_seen_at=first_seen)
    service = NotificationService(sqlite_session_factory, RecordingClient())

    ordinary = await service.load_candidates(BOHDAN_PROFILE, 55)
    enabled = await service.load_candidates(BOHDAN_PROFILE, 55, explore_since=activation)

    assert len(ordinary) == (2 if score >= 55 else 0)
    expected = score >= 55 or (35 <= score <= 54 and offset >= 0)
    assert len(enabled) == (2 if expected else 0)


@pytest.mark.parametrize("main_threshold", [35, 55, 80])
async def test_old_explore_is_never_backfilled_by_a_lower_main_threshold(
    sqlite_session_factory: async_sessionmaker[AsyncSession], main_threshold: int
) -> None:
    activation = datetime.now(UTC)
    await seed_candidate(
        sqlite_session_factory, score=50, first_seen_at=activation - timedelta(days=1)
    )
    service = NotificationService(sqlite_session_factory, RecordingClient())
    assert (
        await service.load_candidates(BOHDAN_PROFILE, main_threshold, explore_since=activation)
        == []
    )


async def test_explore_is_labeled_queued_within_the_existing_limit_and_sent_once(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    activation = datetime.now(UTC) - timedelta(minutes=1)
    await seed_candidate(sqlite_session_factory, score=45, first_seen_at=datetime.now(UTC))
    client = RecordingClient()
    service = NotificationService(sqlite_session_factory, client)
    first = await service.dispatch(BOHDAN_PROFILE, 55, 1, activation, explore_since=activation)
    second = await service.dispatch(BOHDAN_PROFILE, 55, 1, activation, explore_since=activation)
    third = await service.dispatch(BOHDAN_PROFILE, 55, 1, activation, explore_since=activation)

    assert (first.sent, second.sent, third.sent) == (1, 1, 0)
    assert all(message.startswith("<b>Explore:") for message in client.messages)
    assert all("Synthetic uncertainty" in message for message in client.messages)
    async with sqlite_session_factory() as session:
        deliveries = (await session.scalars(select(NotificationDelivery))).all()
        assert len(deliveries) == 2
        assert all(item.status == DeliveryStatus.SENT.value for item in deliveries)


async def test_explore_failed_delivery_retains_the_existing_retry_path(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    activation = datetime.now(UTC) - timedelta(minutes=1)
    await seed_candidate(sqlite_session_factory, score=54, first_seen_at=datetime.now(UTC))
    client = RecordingClient(fail_once=True)
    service = NotificationService(sqlite_session_factory, client)
    summary = await service.dispatch(BOHDAN_PROFILE, 55, 5, activation, explore_since=activation)
    assert summary.failed == 1 and summary.sent == 1
    async with sqlite_session_factory() as session, session.begin():
        failed = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.status == DeliveryStatus.FAILED.value
            )
        )
        assert failed is not None
        failed.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    retried = await service.retry_due(BOHDAN_PROFILE.profile_id, 5)
    assert retried.sent == 1
    assert len(client.messages) == 2
    assert all(message.startswith("<b>Explore:") for message in client.messages)


async def test_timezone_offset_does_not_change_activation_boundary(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    activation = datetime.now(UTC)
    await seed_candidate(sqlite_session_factory, score=35, first_seen_at=activation)
    service = NotificationService(sqlite_session_factory, RecordingClient())
    assert (
        len(
            await service.load_candidates(
                BOHDAN_PROFILE,
                55,
                explore_since=activation.astimezone(timezone(timedelta(hours=2))),
            )
        )
        == 2
    )


async def test_new_explore_waits_for_late_metadata_without_becoming_historical(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    activation = datetime.now(UTC) - timedelta(days=2)
    first_seen = activation + timedelta(hours=1)
    await seed_candidate(sqlite_session_factory, score=24, first_seen_at=first_seen)
    client = RecordingClient()
    service = NotificationService(sqlite_session_factory, client)
    cursor = datetime.now(UTC) - timedelta(hours=1)
    assert (
        await service.dispatch(BOHDAN_PROFILE, 55, 5, cursor, explore_since=activation)
    ).sent == 0
    async with sqlite_session_factory() as session, session.begin():
        for evaluation in (await session.scalars(select(MatchEvaluation))).all():
            evaluation.score = 45
    summary = await service.dispatch(BOHDAN_PROFILE, 55, 5, cursor, explore_since=activation)
    assert summary.sent == 2
    assert len(client.messages) == 2
    assert (
        await service.dispatch(BOHDAN_PROFILE, 55, 5, cursor, explore_since=activation)
    ).sent == 0
