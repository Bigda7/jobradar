from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from statistics import median
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import Source, SourceRun
from jobradar.domain.enums import RunStatus
from jobradar.notifications.telegram import TelegramClient, TelegramDeliveryError
from jobradar.security import redact_sensitive_text

logger = structlog.get_logger(__name__)

type SourceHealthAlertEvent = Literal[
    "failure",
    "recovery",
    "coverage_issue",
    "coverage_recovery",
]
type CoverageIssueReason = Literal["repeated_limit", "discovered_drop"]

LIMIT_STREAK = 3
DROP_STREAK = 2
DROP_BASELINE_RUNS = 5
DROP_MIN_BASELINE = 10
DROP_RATIO = 0.5


@dataclass(frozen=True, slots=True)
class SourceHealthAlertResult:
    event: SourceHealthAlertEvent | None = None
    sent: bool = False


@dataclass(frozen=True, slots=True)
class _CoverageIssue:
    reason: CoverageIssueReason
    baseline_count: int | None = None


@dataclass(frozen=True, slots=True)
class _PendingAlert:
    event: SourceHealthAlertEvent
    source_id: int
    message: str
    failure_alert_active: bool | None = None
    coverage_alert_active: bool | None = None
    coverage_alert_reason: CoverageIssueReason | None = None


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

        try:
            await self._telegram.send_message(alert.message)
        except TelegramDeliveryError as error:
            logger.warning(
                "source_health_alert_delivery_failed",
                source_id=alert.source_id,
                run_id=run_id,
                alert_event=alert.event,
                error=redact_sensitive_text(str(error)),
            )
            return SourceHealthAlertResult(event=alert.event)

        await self._set_alert_state(alert)
        logger.info(
            "source_health_alert_delivered",
            source_id=alert.source_id,
            run_id=run_id,
            alert_event=alert.event,
        )
        return SourceHealthAlertResult(event=alert.event, sent=True)

    async def _load_alert(
        self,
        run_id: int,
    ) -> _PendingAlert | None:
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
                return _PendingAlert(
                    event="recovery",
                    source_id=source.id,
                    message=_format_recovery_message(source, run),
                    failure_alert_active=False,
                )

            if run.status == RunStatus.FAILED.value:
                if source.failure_alert_active:
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
                return _PendingAlert(
                    event="failure",
                    source_id=source.id,
                    message=_format_failure_message(source, run),
                    failure_alert_active=True,
                )

            if run.status != RunStatus.SUCCEEDED.value:
                return None

            successful_runs = list(
                await session.scalars(
                    select(SourceRun)
                    .where(
                        SourceRun.source_id == source.id,
                        SourceRun.status == RunStatus.SUCCEEDED.value,
                    )
                    .order_by(SourceRun.started_at.desc(), SourceRun.id.desc())
                    .limit(DROP_STREAK + DROP_BASELINE_RUNS + 1)
                )
            )
            issue = _coverage_issue(successful_runs)

            if source.coverage_alert_active:
                if issue is not None:
                    return None
                return _PendingAlert(
                    event="coverage_recovery",
                    source_id=source.id,
                    message=_format_coverage_recovery_message(source, run),
                    coverage_alert_active=False,
                )

            if issue is None:
                return None
            previous_issue = _coverage_issue(successful_runs[1:])
            if previous_issue is not None and previous_issue.reason == issue.reason:
                return None
            return _PendingAlert(
                event="coverage_issue",
                source_id=source.id,
                message=_format_coverage_issue_message(source, run, issue),
                coverage_alert_active=True,
                coverage_alert_reason=issue.reason,
            )

    async def _set_alert_state(self, alert: _PendingAlert) -> None:
        async with self._session_factory() as session, session.begin():
            source = await session.get(Source, alert.source_id)
            if source is None:
                raise RuntimeError(f"Source {alert.source_id} does not exist.")
            if alert.failure_alert_active is not None:
                source.failure_alert_active = alert.failure_alert_active
            if alert.coverage_alert_active is not None:
                source.coverage_alert_active = alert.coverage_alert_active
                source.coverage_alert_reason = (
                    alert.coverage_alert_reason if alert.coverage_alert_active else None
                )


def _coverage_issue(runs: list[SourceRun]) -> _CoverageIssue | None:
    if len(runs) >= LIMIT_STREAK and all(run.limit_reached for run in runs[:LIMIT_STREAK]):
        return _CoverageIssue(reason="repeated_limit")

    required_runs = DROP_STREAK + 3
    if len(runs) < required_runs:
        return None
    baseline_values = [
        run.discovered_count for run in runs[DROP_STREAK : DROP_STREAK + DROP_BASELINE_RUNS]
    ]
    if len(baseline_values) < 3:
        return None
    baseline_count = round(median(baseline_values))
    if baseline_count < DROP_MIN_BASELINE:
        return None
    threshold = baseline_count * DROP_RATIO
    if all(run.discovered_count <= threshold for run in runs[:DROP_STREAK]):
        return _CoverageIssue(reason="discovered_drop", baseline_count=baseline_count)
    return None


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


def _format_coverage_issue_message(
    source: Source,
    run: SourceRun,
    issue: _CoverageIssue,
) -> str:
    if issue.reason == "repeated_limit":
        detail = "Три последних запуска достигли настроенного предела выдачи."
    else:
        detail = (
            "Количество обработанных вакансий два запуска подряд ниже половины "
            f"обычного уровня ({run.discovered_count} вместо примерно {issue.baseline_count})."
        )
    return "\n".join(
        (
            f"<b>Неполное покрытие источника: {escape(source.display_name)}</b>",
            detail,
            "Источник работает, но часть вакансий может остаться за пределами текущей выборки.",
            "Повторные сообщения не придут, пока состояние не изменится.",
        )
    )


def _format_coverage_recovery_message(source: Source, run: SourceRun) -> str:
    return "\n".join(
        (
            f"<b>Покрытие источника восстановлено: {escape(source.display_name)}</b>",
            "Выдача вернулась к нормальному уровню.",
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
