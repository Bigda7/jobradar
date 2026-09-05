from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source, SourceRun
from jobradar.domain.enums import OpportunityKind, RunStatus
from jobradar.notifications.source_health import SourceHealthAlertService
from jobradar.notifications.telegram import (
    InlineKeyboardMarkup,
    TelegramClient,
    TelegramDeliveryError,
)


class RecordingTelegramClient(TelegramClient):
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


class FailingOnceTelegramClient(RecordingTelegramClient):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        if not self.failed:
            self.failed = True
            raise TelegramDeliveryError("Temporary Telegram failure")
        return await super().send_message(text, reply_markup, chat_id)


async def _create_source(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    last_success_at: datetime | None,
) -> int:
    async with session_factory() as session, session.begin():
        source = Source(
            name="workua",
            display_name="Work.ua",
            opportunity_kind=OpportunityKind.EMPLOYMENT.value,
            last_success_at=last_success_at,
        )
        session.add(source)
        await session.flush()
        return source.id


async def _create_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    source_id: int,
    status: RunStatus,
    started_at: datetime,
    error_message: str | None = None,
    candidate_count: int = 0,
    discovered_count: int = 0,
) -> int:
    async with session_factory() as session, session.begin():
        run = SourceRun(
            source_id=source_id,
            status=status.value,
            started_at=started_at,
            finished_at=started_at,
            error_message=error_message,
            candidate_count=candidate_count,
            discovered_count=discovered_count,
        )
        session.add(run)
        source = await session.get(Source, source_id)
        assert source is not None
        source.last_run_at = started_at
        source.last_error = error_message
        if status is RunStatus.SUCCEEDED:
            source.last_success_at = started_at
        await session.flush()
        return run.id


@pytest.mark.asyncio
async def test_source_health_alerts_after_two_failures_and_once_on_recovery(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started_at = datetime(2026, 9, 5, 8, tzinfo=UTC)
    source_id = await _create_source(
        sqlite_session_factory,
        last_success_at=started_at - timedelta(hours=3),
    )
    telegram = RecordingTelegramClient()
    service = SourceHealthAlertService(sqlite_session_factory, telegram)

    first_failure_id = await _create_run(
        sqlite_session_factory,
        source_id=source_id,
        status=RunStatus.FAILED,
        started_at=started_at,
        error_message="Search page unavailable",
    )
    first_result = await service.process_run(first_failure_id)

    second_failure_id = await _create_run(
        sqlite_session_factory,
        source_id=source_id,
        status=RunStatus.FAILED,
        started_at=started_at + timedelta(hours=1),
        error_message="Search page unavailable",
    )
    failure_result = await service.process_run(second_failure_id)
    duplicate_result = await service.process_run(second_failure_id)

    assert first_result.event is None
    assert failure_result.event == "failure"
    assert failure_result.sent is True
    assert duplicate_result.event is None
    assert len(telegram.messages) == 1
    assert "Проблема с источником: Work.ua" in telegram.messages[0]
    assert "Два последних запуска завершились ошибкой." in telegram.messages[0]
    assert "05.09.2026 05:00 UTC" in telegram.messages[0]

    recovery_id = await _create_run(
        sqlite_session_factory,
        source_id=source_id,
        status=RunStatus.SUCCEEDED,
        started_at=started_at + timedelta(hours=2),
        candidate_count=84,
        discovered_count=67,
    )
    recovery_result = await service.process_run(recovery_id)
    duplicate_recovery_result = await service.process_run(recovery_id)

    assert recovery_result.event == "recovery"
    assert recovery_result.sent is True
    assert duplicate_recovery_result.event is None
    assert len(telegram.messages) == 2
    assert "Источник восстановлен: Work.ua" in telegram.messages[1]
    assert "Получено кандидатов: 84." in telegram.messages[1]
    assert "Обработано вакансий: 67." in telegram.messages[1]

    async with sqlite_session_factory() as session:
        source = await session.get(Source, source_id)
        assert source is not None
        assert source.failure_alert_active is False


@pytest.mark.asyncio
async def test_failed_source_alert_delivery_is_retried(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    started_at = datetime(2026, 9, 5, 8, tzinfo=UTC)
    source_id = await _create_source(sqlite_session_factory, last_success_at=None)
    await _create_run(
        sqlite_session_factory,
        source_id=source_id,
        status=RunStatus.FAILED,
        started_at=started_at,
        error_message="Search page unavailable",
    )
    second_failure_id = await _create_run(
        sqlite_session_factory,
        source_id=source_id,
        status=RunStatus.FAILED,
        started_at=started_at + timedelta(hours=1),
        error_message="Search page unavailable",
    )
    telegram = FailingOnceTelegramClient()
    service = SourceHealthAlertService(sqlite_session_factory, telegram)

    failed_result = await service.process_run(second_failure_id)
    retry_result = await service.process_run(second_failure_id)

    assert failed_result.event == "failure"
    assert failed_result.sent is False
    assert retry_result.event == "failure"
    assert retry_result.sent is True
    assert len(telegram.messages) == 1
