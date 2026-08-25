import argparse
import asyncio
import signal
from datetime import UTC, datetime

import structlog

from jobradar.config import get_settings
from jobradar.db.locks import try_transaction_advisory_lock
from jobradar.db.session import engine, session_factory
from jobradar.ingestion.deduplication import CrossSourceDeduplicationService
from jobradar.ingestion.service import IngestionService
from jobradar.logging_config import configure_logging
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.currency import NbuExchangeRateClient
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import TelegramClient
from jobradar.opportunities.expiration import StaleExpirationService
from jobradar.sources.registry import build_source_registry

logger = structlog.get_logger(__name__)
WORKER_CYCLE_LOCK_KEY = 0x4A4F425241444152


class WorkerCycleLockUnavailable(RuntimeError):
    pass


async def run_cycle(*, force_sources: bool = False) -> None:
    settings = get_settings()
    cycle_started_at = datetime.now(UTC)
    ingestion = IngestionService(
        session_factory,
        reconciliation_max_missing_ratio=settings.source_reconciliation_max_missing_ratio,
    )
    sources = build_source_registry(settings)
    await ingestion.synchronize_enabled_sources(sources)
    for source in sources:
        poll_interval_seconds = settings.source_poll_interval_seconds(source.name)
        if force_sources or await ingestion.is_source_due(
            source.name,
            poll_interval_seconds,
            jitter_ratio=settings.source_poll_jitter_ratio,
            now=cycle_started_at,
        ):
            await ingestion.run_source(source)
        else:
            logger.info(
                "source_run_skipped_not_due",
                source=source.name,
                poll_interval_seconds=poll_interval_seconds,
                jitter_ratio=settings.source_poll_jitter_ratio,
            )

    deduplication_summary = await CrossSourceDeduplicationService(session_factory).merge_existing()
    logger.info(
        "cross_source_deduplication_finished",
        duplicate_groups=deduplication_summary.duplicate_groups,
        merged_opportunities=deduplication_summary.merged_opportunities,
    )

    expiration_summary = await StaleExpirationService(session_factory).expire_stale(
        employment_days=settings.employment_stale_after_days,
        freelance_days=settings.freelance_stale_after_days,
        now=cycle_started_at,
    )
    logger.info(
        "stale_expiration_finished",
        employment_days=settings.employment_stale_after_days,
        freelance_days=settings.freelance_stale_after_days,
        expired_employment=expiration_summary.expired_employment,
        expired_freelance=expiration_summary.expired_freelance,
        protected_favorites=expiration_summary.protected_favorites,
    )

    if not settings.matching_enabled:
        return
    matching_summary = await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    logger.info(
        "matching_cycle_finished",
        profile=BOHDAN_PROFILE.profile_id,
        rules_version=BOHDAN_PROFILE.rules_version,
        evaluated=matching_summary.evaluated,
        unchanged=matching_summary.unchanged,
    )

    if not settings.telegram_enabled:
        return
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise RuntimeError("Telegram is enabled without complete credentials.")
    telegram_client = TelegramClient(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
        request_timeout_seconds=settings.telegram_request_timeout_seconds,
    )
    notification_summary = await NotificationService(
        session_factory,
        telegram_client,
        NbuExchangeRateClient(
            rates_url=settings.nbu_rates_url,
            request_timeout_seconds=settings.nbu_request_timeout_seconds,
        ),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=settings.matching_min_score,
        max_messages=settings.telegram_max_messages_per_cycle,
        minimum_first_seen_at=(None if settings.telegram_notify_existing else cycle_started_at),
    )
    logger.info(
        "notification_cycle_finished",
        channel="telegram",
        considered=notification_summary.considered,
        sent=notification_summary.sent,
        failed=notification_summary.failed,
        skipped_historical=notification_summary.skipped_historical,
        skipped_duplicate=notification_summary.skipped_duplicate,
        skipped_paused=notification_summary.skipped_paused,
    )


async def run_worker_cycle(
    *,
    force_sources: bool,
    failure_retry_seconds: int,
) -> bool:
    try:
        async with try_transaction_advisory_lock(
            engine,
            WORKER_CYCLE_LOCK_KEY,
        ) as lock_acquired:
            if not lock_acquired:
                raise WorkerCycleLockUnavailable(
                    "Another JobRadar worker cycle already holds the advisory lock."
                )
            await run_cycle(force_sources=force_sources)
    except Exception as error:
        logger.exception(
            "worker_cycle_failed",
            error=str(error),
            retry_seconds=failure_retry_seconds,
        )
        if force_sources:
            raise
        return False
    logger.info("worker_cycle_finished")
    return True


async def run_worker(run_once: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            logger.info("worker_cycle_started")
            cycle_succeeded = await run_worker_cycle(
                force_sources=run_once,
                failure_retry_seconds=settings.worker_failure_retry_seconds,
            )
            if run_once:
                break
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=(
                        settings.worker_interval_seconds
                        if cycle_succeeded
                        else settings.worker_failure_retry_seconds
                    ),
                )
            except TimeoutError:
                continue
    finally:
        await engine.dispose()
        logger.info("worker_stopped")


async def run_telegram_test() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise RuntimeError("Telegram credentials are not configured.")
    client = TelegramClient(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
        request_timeout_seconds=settings.telegram_request_timeout_seconds,
    )
    bot = await client.get_me()
    message_id = await client.send_message(
        "<b>Проверка Telegram-бота JobRadar</b>\nКанал уведомлений настроен правильно."
    )
    logger.info(
        "telegram_test_succeeded",
        bot_username=bot.get("username"),
        message_id=message_id,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run JobRadar source ingestion.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one ingestion cycle and exit.")
    mode.add_argument(
        "--test-telegram",
        action="store_true",
        help="Validate Telegram credentials and send one test message.",
    )
    arguments = parser.parse_args()
    if arguments.test_telegram:
        asyncio.run(run_telegram_test())
    else:
        asyncio.run(run_worker(run_once=arguments.once))


if __name__ == "__main__":
    main()
