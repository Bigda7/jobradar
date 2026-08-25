from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.db.models import NotificationDelivery
from jobradar.domain.enums import DeliveryStatus, OpportunityKind
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.currency import CurrencyConversionError, ExchangeRates
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import (
    NotificationCandidate,
    NotificationService,
    format_match_message,
)
from jobradar.notifications.telegram import InlineKeyboardMarkup, TelegramClient
from jobradar.sources.freelancer import FreelancerApiClient, FreelancerSource
from jobradar.sources.mock import DEFAULT_LISTINGS, MockSource


class RecordingTelegramClient(TelegramClient):
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reply_markups: list[InlineKeyboardMarkup | None] = []

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.messages.append(text)
        self.reply_markups.append(reply_markup)
        return len(self.messages)


TEST_RATES = ExchangeRates(
    {
        "USD": Decimal("40"),
        "UAH": Decimal("1"),
        "CZK": Decimal("2"),
    },
    effective_date="22.08.2026",
)


class FixedExchangeRateProvider:
    async def fetch_rates(self) -> ExchangeRates:
        return TEST_RATES


class FailingExchangeRateProvider:
    async def fetch_rates(self) -> ExchangeRates:
        raise CurrencyConversionError("NBU is unavailable")


@pytest.mark.asyncio
async def test_telegram_client_uses_bot_api_json_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1, "is_bot": True}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramClient(
            bot_token="test-token",
            chat_id=123,
            client=http_client,
            api_base_url="https://telegram.test",
        )
        bot = await client.get_me()
        message_id = await client.send_message("<b>Test</b>")

    assert bot["is_bot"] is True
    assert message_id == 42
    assert len(requests) == 2
    assert requests[1].method == "POST"
    assert b'"parse_mode":"HTML"' in requests[1].content
    assert b'"chat_id":123' in requests[1].content


@pytest.mark.asyncio
async def test_notification_delivery_is_idempotent(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert first.sent == 2
    assert second.sent == 0
    assert second.skipped_duplicate == 2
    assert len(client.messages) == 2
    assert all(markup is not None for markup in client.reply_markups)
    assert all(len(markup["inline_keyboard"][0]) == 3 for markup in client.reply_markups if markup)
    assert all("[Mock Source] Вакансия" in message for message in client.messages)
    assert any("Локация: Удалённо, Европа" in message for message in client.messages)
    assert any("- USD: 1,200-1,800 / месяц" in message for message in client.messages)
    assert any("- UAH: 48,000-72,000 / месяц" in message for message in client.messages)
    assert any("- CZK: 24,000-36,000 / месяц" in message for message in client.messages)
    async with sqlite_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 2


@pytest.mark.asyncio
async def test_notification_delivery_falls_back_to_original_currency(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()

    result = await NotificationService(
        sqlite_session_factory,
        client,
        FailingExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert result.sent == 2
    assert result.failed == 0
    assert any("- USD: 1,200-1,800 / месяц" in message for message in client.messages)
    assert all("- UAH:" not in message for message in client.messages)


@pytest.mark.asyncio
async def test_manual_candidate_lists_exclude_inactive_opportunities(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    await ingestion.run_source(MockSource((DEFAULT_LISTINGS[0],)))
    service = NotificationService(
        sqlite_session_factory,
        RecordingTelegramClient(),
        FixedExchangeRateProvider(),
    )

    candidates = await service.load_candidates(
        BOHDAN_PROFILE,
        BOHDAN_PROFILE.notification_threshold,
    )

    assert [candidate.title for candidate in candidates] == ["Junior Full-Stack Developer"]


@pytest.mark.asyncio
async def test_updated_listing_does_not_send_the_same_opportunity_again(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ingestion = IngestionService(sqlite_session_factory)
    await ingestion.run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())
    first = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )
    assert first.sent == 2

    changed_listings = deepcopy(DEFAULT_LISTINGS)
    changed_listings[0]["description"] += " Updated requirements."
    await ingestion.run_source(MockSource(changed_listings))
    matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    second = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert matching.evaluated == 1
    assert second.sent == 0
    assert second.skipped_duplicate == 2
    assert len(client.messages) == 2


@pytest.mark.asyncio
async def test_notification_delivery_skips_historical_matches(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()

    result = await NotificationService(
        sqlite_session_factory,
        client,
        FixedExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=datetime.now(UTC) + timedelta(seconds=1),
    )

    assert result.sent == 0
    assert result.skipped_historical == 2
    assert client.messages == []


@pytest.mark.asyncio
async def test_paused_notifications_are_recorded_and_not_sent_after_resume(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    preferences = NotificationPreferenceService(sqlite_session_factory)
    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", True)
    await IngestionService(sqlite_session_factory).run_source(MockSource())
    await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    service = NotificationService(sqlite_session_factory, client, FixedExchangeRateProvider())

    paused = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=1,
        minimum_first_seen_at=None,
    )

    assert paused.considered == 2
    assert paused.skipped_paused == 2
    assert paused.sent == 0
    assert client.messages == []
    async with sqlite_session_factory() as session:
        statuses = list(await session.scalars(select(NotificationDelivery.status)))
    assert statuses == [
        DeliveryStatus.SKIPPED_PAUSED.value,
        DeliveryStatus.SKIPPED_PAUSED.value,
    ]

    await preferences.set_paused(BOHDAN_PROFILE.profile_id, "telegram", False)
    resumed = await service.dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=5,
        minimum_first_seen_at=None,
    )

    assert resumed.sent == 0
    assert resumed.skipped_duplicate == 2
    assert client.messages == []


def test_freelance_notification_uses_project_specific_template() -> None:
    candidate = NotificationCandidate(
        opportunity_id=101,
        kind=OpportunityKind.FREELANCE_PROJECT,
        title="Django API integration",
        company="Verified Employer",
        location_text="Remote",
        employment_type=None,
        contract_type="fixed",
        salary_min=Decimal("300"),
        salary_max=Decimal("600"),
        salary_currency="USD",
        salary_period="project",
        first_seen_at=datetime.now(UTC),
        source_display_name="Freelancer.com",
        source_url="https://www.freelancer.com/projects/python/django-api-integration",
        content_hash="a" * 64,
        score=82,
        reasons=(
            "Совпавшие навыки: Python, Django, REST APIs.",
            "Фиксированный бюджет достигает предпочтительного диапазона: USD 300-600.",
        ),
        concerns=("Высокая конкуренция: 65 ставок.",),
        raw_data={
            "bid_stats": {"bid_count": 65},
            "_owner": {
                "status": {"payment_verified": True},
                "employer_reputation": {"entire_history": {"overall": 4.8, "reviews": 24}},
            },
        },
    )

    message = format_match_message(candidate, TEST_RATES)

    assert "[Freelancer.com] Фриланс-проект: 82/100" in message
    assert "Тип проекта: Фиксированная цена" in message
    assert "<b>Бюджет</b>" in message
    assert "- USD: 300-600 / проект" in message
    assert "- UAH: 12,000-24,000 / проект" in message
    assert "- CZK: 6,000-12,000 / проект" in message
    assert "Конкуренция: 65 ставок" in message
    assert "Статус заказчика: платёжные данные подтверждены" in message
    assert "рейтинг 4.8/5 на основе 24 отзывов" in message
    assert ">Открыть проект</a>" in message


@pytest.mark.asyncio
async def test_freelance_match_is_delivered_with_freelance_template(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payload = {
        "status": "success",
        "result": {
            "total_count": 1,
            "projects": [
                {
                    "id": 9001,
                    "owner_id": 8001,
                    "title": "Small Django REST API webhook integration",
                    "description": "Build a small React dashboard and PostgreSQL webhook.",
                    "seo_url": "python/small-django-api-integration",
                    "type": "fixed",
                    "local": False,
                    "language": "en",
                    "submitdate": 1787385600,
                    "budget": {"minimum": 300, "maximum": 600},
                    "currency": {"code": "USD", "exchange_rate": 1.0},
                    "jobs": [
                        {"name": "Django"},
                        {"name": "React.js"},
                        {"name": "PostgreSQL"},
                    ],
                    "bid_stats": {"bid_count": 5},
                }
            ],
            "users": {
                "8001": {
                    "display_name": "Verified Employer",
                    "status": {"payment_verified": True},
                }
            },
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http_client:
        source = FreelancerSource(
            api_client=FreelancerApiClient(
                oauth_token="test-oauth-token",
                api_base_url="https://freelancer.test/api/projects/0.1",
                client=http_client,
            ),
            search_queries=("django react",),
            web_base_url="https://freelancer.test",
        )
        await IngestionService(sqlite_session_factory).run_source(source)

    matching = await MatchingService(sqlite_session_factory).evaluate(BOHDAN_PROFILE)
    client = RecordingTelegramClient()
    delivery = await NotificationService(
        sqlite_session_factory,
        client,
        FixedExchangeRateProvider(),
    ).dispatch(
        profile=BOHDAN_PROFILE,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        max_messages=3,
        minimum_first_seen_at=None,
    )

    assert matching.evaluated == 1
    assert delivery.sent == 1
    assert len(client.messages) == 1
    assert "[Freelancer.com] Фриланс-проект" in client.messages[0]
    assert "Конкуренция: 5 ставок" in client.messages[0]
    assert ">Открыть проект</a>" in client.messages[0]
