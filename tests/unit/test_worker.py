from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar import worker
from jobradar.db.models import (
    MatchEvaluation,
    NotificationDelivery,
    NotificationScanCursor,
    Opportunity,
    TelegramOpportunityMessage,
)
from jobradar.domain.enums import DeliveryStatus
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.notifications.currency import CurrencyConversionError, ExchangeRates
from jobradar.notifications.scan_cursor import NotificationScanCursorService
from jobradar.sources.mock import MockSource


@asynccontextmanager
async def _acquired_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
    yield True


@pytest.mark.asyncio
async def test_only_start_scheduled_source_disables_completion_jitter(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class StartScheduledSource(MockSource):
        name = "djinni"
        poll_from_start = True

    class CompletionScheduledSource(MockSource):
        name = "workua"

    settings = SimpleNamespace(
        matching_enabled=False,
        telegram_enabled=False,
        source_reconciliation_max_missing_ratio=0.8,
        source_poll_jitter_ratio=0.15,
        source_poll_interval_seconds=lambda name: 900 if name == "djinni" else 21600,
        employment_stale_after_days=365,
        freelance_stale_after_days=365,
    )
    checks = []

    async def not_due(self, name, interval, **kwargs):  # type: ignore[no-untyped-def]
        checks.append((name, interval, kwargs["jitter_ratio"], kwargs["schedule_from_start"]))
        return False

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "session_factory", sqlite_session_factory)
    monkeypatch.setattr(
        worker,
        "build_source_registry",
        lambda _: [
            StartScheduledSource(),
            CompletionScheduledSource(),
        ],
    )
    monkeypatch.setattr(IngestionService, "is_source_due", not_due)
    await worker.run_cycle()
    assert checks == [("djinni", 900, 0, True), ("workua", 21600, 0.15, False)]


@pytest.mark.asyncio
async def test_background_worker_cycle_contains_failures_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_cycle(*, force_sources: bool = False) -> None:
        raise RuntimeError("simulated cycle failure")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)
    monkeypatch.setattr(worker, "run_cycle", failing_cycle)

    succeeded = await worker.run_worker_cycle(
        force_sources=False,
        failure_retry_seconds=30,
    )

    assert succeeded is False


@pytest.mark.asyncio
async def test_manual_worker_cycle_returns_a_nonzero_failure_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_cycle(*, force_sources: bool = False) -> None:
        raise RuntimeError("simulated manual failure")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)
    monkeypatch.setattr(worker, "run_cycle", failing_cycle)

    with pytest.raises(RuntimeError, match="simulated manual failure"):
        await worker.run_worker_cycle(
            force_sources=True,
            failure_retry_seconds=30,
        )


@pytest.mark.asyncio
async def test_worker_cycle_refuses_to_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def unavailable_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield False

    async def unexpected_cycle(*, force_sources: bool = False) -> None:
        raise AssertionError("run_cycle must not start without the lock")

    monkeypatch.setattr(worker, "try_transaction_advisory_lock", unavailable_lock)
    monkeypatch.setattr(worker, "run_cycle", unexpected_cycle)

    with pytest.raises(worker.WorkerCycleLockUnavailable):
        await worker.run_worker_cycle(
            force_sources=True,
            failure_retry_seconds=30,
        )


@pytest.mark.asyncio
async def test_worker_delivers_new_matches_once_after_exchange_rate_outage(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = SimpleNamespace(
        source_reconciliation_max_missing_ratio=0.8,
        source_poll_jitter_ratio=0.0,
        source_poll_interval_seconds=lambda _: 3600,
        telegram_enabled=True,
        telegram_bot_token=SecretStr("test-token"),
        telegram_chat_id=123,
        telegram_request_timeout_seconds=1.0,
        telegram_source_health_alerts_enabled=False,
        employment_stale_after_days=365,
        freelance_stale_after_days=365,
        matching_enabled=True,
        matching_min_score=55,
        telegram_max_messages_per_cycle=5,
        telegram_notify_existing=False,
        nbu_rates_url="https://bank.test/rates",
        nbu_request_timeout_seconds=1.0,
    )
    messages: list[str] = []

    class FakeTelegramClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def send_message(
            self,
            text: str,
            reply_markup: object = None,
            chat_id: int | None = None,
        ) -> int:
            messages.append(text)
            return len(messages)

    class RecoveringRateClient:
        requests = 0

        def __init__(self, **kwargs: object) -> None:
            pass

        async def fetch_rates(self) -> ExchangeRates:
            self.requests += 1
            if self.requests <= 2:
                raise CurrencyConversionError("NBU is unavailable")
            return ExchangeRates({"USD": Decimal("40"), "CZK": Decimal("2")})

    class InterruptedCursor(NotificationScanCursorService):
        remaining_failures = 1

        async def complete_cycle(self, profile_id: str, channel: str, started_at: datetime) -> None:
            if InterruptedCursor.remaining_failures:
                InterruptedCursor.remaining_failures -= 1
                raise RuntimeError("interrupted after dispatch")
            await super().complete_cycle(profile_id, channel, started_at)

    rate_client = RecoveringRateClient()
    monkeypatch.setattr(worker, "session_factory", sqlite_session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "build_source_registry", lambda _: (MockSource(),))
    monkeypatch.setattr(worker, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(worker, "NbuExchangeRateClient", lambda **_: rate_client)
    monkeypatch.setattr(worker, "NotificationScanCursorService", InterruptedCursor)
    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)

    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is False
    assert messages == []
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MatchEvaluation)) == 0
        cursor = await session.get(NotificationScanCursor, ("bohdan", "telegram"))
        assert cursor is not None
        pending_cutoff = cursor.minimum_first_seen_at

    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is False
    async with sqlite_session_factory() as session:
        cursor = await session.get(NotificationScanCursor, ("bohdan", "telegram"))
        assert cursor is not None
        assert cursor.minimum_first_seen_at == pending_cutoff
    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is False
    assert len(messages) == 2
    async with sqlite_session_factory() as session:
        cursor = await session.get(NotificationScanCursor, ("bohdan", "telegram"))
        assert cursor is not None
        assert cursor.minimum_first_seen_at == pending_cutoff
    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is True
    assert len(messages) == 2
    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is True
    assert len(messages) == 2
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 2
        cursor = await session.get(NotificationScanCursor, ("bohdan", "telegram"))
        assert cursor is not None
        assert cursor.minimum_first_seen_at >= pending_cutoff


@pytest.mark.asyncio
async def test_worker_retries_stored_delivery_before_exchange_rate_failure(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    async with sqlite_session_factory() as session, session.begin():
        opportunity_id = await session.scalar(select(Opportunity.id).limit(1))
        assert opportunity_id is not None
        session.add(
            NotificationDelivery(
                opportunity_id=opportunity_id,
                profile_id=BOHDAN_PROFILE.profile_id,
                channel="telegram",
                event_key="stored-match",
                status=DeliveryStatus.FAILED.value,
                attempts=3,
                next_attempt_at=datetime.now(UTC) - timedelta(minutes=1),
                message_text="<b>Stored match</b>",
                source_url="https://example.invalid/job",
            )
        )

    settings = SimpleNamespace(
        source_reconciliation_max_missing_ratio=0.8,
        source_poll_jitter_ratio=0.0,
        source_poll_interval_seconds=lambda _: 3600,
        telegram_enabled=True,
        telegram_bot_token=SecretStr("test-token"),
        telegram_chat_id=123,
        telegram_request_timeout_seconds=1.0,
        telegram_source_health_alerts_enabled=False,
        employment_stale_after_days=365,
        freelance_stale_after_days=365,
        matching_enabled=True,
        matching_min_score=55,
        telegram_max_messages_per_cycle=5,
        telegram_notify_existing=False,
        nbu_rates_url="https://bank.test/rates",
        nbu_request_timeout_seconds=1.0,
    )
    messages: list[str] = []

    class FakeTelegramClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def send_message(
            self, text: str, reply_markup: object = None, chat_id: int | None = None
        ) -> int:
            messages.append(text)
            return len(messages)

    class UnavailableRates:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def fetch_rates(self) -> ExchangeRates:
            raise CurrencyConversionError("NBU is unavailable")

    monkeypatch.setattr(worker, "session_factory", sqlite_session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "build_source_registry", lambda _: ())
    monkeypatch.setattr(worker, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(worker, "NbuExchangeRateClient", UnavailableRates)
    monkeypatch.setattr(worker, "try_transaction_advisory_lock", _acquired_lock)

    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is False
    assert await worker.run_worker_cycle(force_sources=False, failure_retry_seconds=1) is False
    assert messages == ["<b>Stored match</b>"]
    async with sqlite_session_factory() as session:
        delivery = await session.scalar(select(NotificationDelivery))
        assert delivery is not None
        assert delivery.status == DeliveryStatus.SENT.value
        assert delivery.attempts == 4
        recorded_count = await session.scalar(
            select(func.count()).select_from(TelegramOpportunityMessage)
        )
        assert recorded_count == 1
