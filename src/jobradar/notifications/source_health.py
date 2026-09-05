from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source, SourceRun
from jobradar.domain.enums import RunStatus
from jobradar.notifications.telegram import TelegramClient, TelegramDeliveryError
from jobradar.security import redact_sensitive_text

logger = structlog.get_logger(__name__)

type SourceHealthAlertEvent = Literal["failure", "recovery"]


@dataclass(frozen=True, slots=True)
class SourceHealthAlertResult:
    event: SourceHealthAlertEvent | None = None
    sent: bool = False


class SourceHealthAlertService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        telegram_client: TelegramClient,
    ) -> None:
        self._session_factory = session_factory
        self._telegram = telegram_client

    async def process_run(self, run_id: int) -> SourceHealthAlertResult:
        alert = await self._load_alert(run_id)
        if alert is None:
            return SourceHealthAlertResult()

        event, source_id, message, alert_active = alert
        try:
            await self._telegram.send_message(message)
        except TelegramDeliveryError as error:
            logger.warning(
                "source_health_alert_delivery_failed",
                source_id=source_id,
                run_id=run_id,
                alert_event=event,
                error=redact_sensitive_text(str(error)),
            )
            return SourceHealthAlertResult(event=event)

        await self._set_alert_state(source_id, alert_active)
        logger.info(
            "source_health_alert_delivered",
            source_id=source_id,
            run_id=run_id,
            alert_event=event,
        )
        return SourceHealthAlertResult(event=event, sent=True)

    async def _load_alert(
        self,
        run_id: int,
    ) -> tuple[SourceHealthAlertEvent, int, str, bool] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(SourceRun, Source)
                    .join(Source, Source.id == SourceRun.source_id)
                    .where(SourceRun.id == run_id)
                )
            ).one_or_none()
            if row is None:
                raise RuntimeError(f"Source run {run_id} does not exist.")
            run, source = row

            if run.status == RunStatus.SUCCEEDED.value and source.failure_alert_active:
                return (
                    "recovery",
                    source.id,
                    _format_recovery_message(source, run),
                    False,
                )

            if run.status != RunStatus.FAILED.value or source.failure_alert_active:
                return None

            recent_statuses = list(
                await session.scalars(
                    select(SourceRun.status)
                    .where(SourceRun.source_id == source.id)
                    .order_by(SourceRun.started_at.desc(), SourceRun.id.desc())
                    .limit(2)
                )
            )
            if len(recent_statuses) < 2 or any(
                status != RunStatus.FAILED.value for status in recent_statuses
            ):
                return None

            return (
                "failure",
                source.id,
                _format_failure_message(source, run),
                True,
            )

    async def _set_alert_state(self, source_id: int, active: bool) -> None:
        async with self._session_factory() as session, session.begin():
            source = await session.get(Source, source_id)
            if source is None:
                raise RuntimeError(f"Source {source_id} does not exist.")
            source.failure_alert_active = active


def _format_failure_message(source: Source, run: SourceRun) -> str:
    reason = redact_sensitive_text(run.error_message or source.last_error or "Unknown error")
    return "\n".join(
        (
            f"<b>Проблема с источником: {escape(source.display_name)}</b>",
            "Два последних запуска завершились ошибкой.",
            f"Последний успешный сбор: {_format_timestamp(source.last_success_at)}.",
            f"Причина: {escape(reason[:500])}",
            "Новые вакансии с этой площадки временно могут не поступать.",
        )
    )


def _format_recovery_message(source: Source, run: SourceRun) -> str:
    return "\n".join(
        (
            f"<b>Источник восстановлен: {escape(source.display_name)}</b>",
            "Сбор снова работает.",
            f"Получено кандидатов: {run.candidate_count}.",
            f"Обработано вакансий: {run.discovered_count}.",
        )
    )


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "успешных запусков ещё не было"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
