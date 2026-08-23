from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobradar.bot import BOT_COMMANDS, TelegramBotService
from jobradar.domain.enums import OpportunityDisposition
from jobradar.ingestion.service import IngestionService
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.matching.service import MatchingService
from jobradar.notifications.currency import ExchangeRates
from jobradar.notifications.messages import TelegramMessageRegistry
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import NotificationService
from jobradar.notifications.telegram import InlineKeyboardMarkup, TelegramClient
from jobradar.opportunities.service import OpportunityStateService
from jobradar.sources.mock import MockSource


class FixedExchangeRateProvider:
    async def fetch_rates(self) -> ExchangeRates:
        return ExchangeRates({"USD": Decimal("40"), "CZK": Decimal("2")})


class RecordingBotClient(TelegramClient):
    def __init__(self) -> None:
        self.messages: list[tuple[str, InlineKeyboardMarkup | None]] = []
        self.callback_answers: list[tuple[str, str]] = []
        self.edits: list[tuple[int, int, InlineKeyboardMarkup]] = []
        self.commands: list[tuple[str, str]] = []
        self.deleted_message_ids: list[int] = []

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        self.messages.append((text, reply_markup))
        return len(self.messages)

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        self.callback_answers.append((callback_query_id, text))

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        self.edits.append((chat_id, message_id, reply_markup))

    async def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        self.commands = list(commands)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        assert chat_id == 123
        self.deleted_message_ids.append(message_id)

    async def get_updates(
        self,
        offset: int | None,
        timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        return []


async def _bot(
    session_factory: async_sessionmaker[AsyncSession],
    latest_limit: int = 5,
) -> tuple[TelegramBotService, RecordingBotClient, OpportunityStateService, list[int]]:
    await IngestionService(session_factory).run_source(MockSource())
    await MatchingService(session_factory).evaluate(BOHDAN_PROFILE)
    telegram = RecordingBotClient()
    rates = FixedExchangeRateProvider()
    notifications = NotificationService(session_factory, telegram, rates)
    states = OpportunityStateService(session_factory)
    candidates = await notifications.load_candidates(
        BOHDAN_PROFILE,
        BOHDAN_PROFILE.notification_threshold,
    )
    service = TelegramBotService(
        telegram_client=telegram,
        notification_service=notifications,
        state_service=states,
        message_registry=TelegramMessageRegistry(session_factory),
        notification_preferences=NotificationPreferenceService(session_factory),
        exchange_rate_provider=rates,
        allowed_chat_id=123,
        minimum_score=BOHDAN_PROFILE.notification_threshold,
        latest_limit=latest_limit,
        poll_timeout_seconds=1,
    )
    return service, telegram, states, [item.opportunity_id for item in candidates]


@pytest.mark.asyncio
async def test_callbacks_persist_favorite_and_hidden_state(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, telegram, states, opportunity_ids = await _bot(sqlite_session_factory)
    opportunity_id = opportunity_ids[0]
    callback = {
        "id": "callback-1",
        "data": f"favorite:{opportunity_id}",
        "message": {"message_id": 10, "chat": {"id": 123}},
    }

    await bot.handle_update({"update_id": 1, "callback_query": callback})

    assert await states.get_disposition(opportunity_id) is OpportunityDisposition.FAVORITE
    assert telegram.callback_answers[-1] == ("callback-1", "Добавлено в избранное.")
    favorite_buttons = telegram.edits[-1][2]["inline_keyboard"][0]
    assert favorite_buttons[0]["text"] == "В избранном \u2b50"

    callback["id"] = "callback-2"
    callback["data"] = f"hide:{opportunity_id}"
    await bot.handle_update({"update_id": 2, "callback_query": callback})

    assert await states.get_disposition(opportunity_id) is OpportunityDisposition.HIDDEN
    assert telegram.callback_answers[-1][1].startswith("Скрыто")
    hidden_buttons = telegram.edits[-1][2]["inline_keyboard"][0]
    assert hidden_buttons == [
        {"text": "Восстановить \U0001f504", "callback_data": f"restore:{opportunity_id}"},
        {"text": "Ссылка", "url": await states.source_url(opportunity_id)},
    ]
    visible = await bot._notifications.load_candidates(
        BOHDAN_PROFILE,
        BOHDAN_PROFILE.notification_threshold,
    )
    assert opportunity_id not in {item.opportunity_id for item in visible}

    callback["id"] = "callback-3"
    callback["data"] = f"restore:{opportunity_id}"
    await bot.handle_update({"update_id": 3, "callback_query": callback})

    assert await states.get_disposition(opportunity_id) is OpportunityDisposition.NEW
    assert telegram.callback_answers[-1] == ("callback-3", "Вакансия восстановлена.")
    restored_buttons = telegram.edits[-1][2]["inline_keyboard"][0]
    assert restored_buttons[0]["callback_data"] == f"favorite:{opportunity_id}"
    assert restored_buttons[1]["callback_data"] == f"hide:{opportunity_id}"


@pytest.mark.asyncio
async def test_favorites_latest_and_stats_commands(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, telegram, states, opportunity_ids = await _bot(sqlite_session_factory)
    await states.toggle_favorite(opportunity_ids[0])

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/favorites"}})
    assert "<b>Избранное: 1</b>" in telegram.messages[-1][0]

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/stats"}})
    assert "Собрано возможностей: 2" in telegram.messages[-1][0]
    assert "В избранном: 1" in telegram.messages[-1][0]

    message_count = len(telegram.messages)
    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/latest"}})
    latest_messages = telegram.messages[message_count:]
    assert latest_messages[0][0] == "Последние подходящие вакансии и проекты: 2"
    assert len(latest_messages) == 3
    assert all(reply_markup is not None for _, reply_markup in latest_messages[1:])

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/clear"}})
    assert telegram.deleted_message_ids == [5, 4]
    assert telegram.messages[-1][0] == "Удалено сообщений с вакансиями: 2."


@pytest.mark.asyncio
async def test_reset_hidden_restores_all_hidden_opportunities(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, _, states, opportunity_ids = await _bot(sqlite_session_factory)
    for opportunity_id in opportunity_ids:
        assert await states.hide(opportunity_id) is True

    assert await states.reset_hidden() == len(opportunity_ids)
    for opportunity_id in opportunity_ids:
        assert await states.get_disposition(opportunity_id) is OpportunityDisposition.NEW


@pytest.mark.asyncio
async def test_all_command_has_no_latest_limit_and_excludes_hidden_opportunities(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, telegram, states, opportunity_ids = await _bot(sqlite_session_factory, latest_limit=1)

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/latest"}})
    assert telegram.messages[0][0] == "Последние подходящие вакансии и проекты: 1"
    assert len(telegram.messages) == 2

    message_count = len(telegram.messages)
    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/all"}})
    all_messages = telegram.messages[message_count:]
    assert all_messages[0][0] == "Все подходящие вакансии и проекты: 2"
    assert len(all_messages) == 3
    assert all(reply_markup is not None for _, reply_markup in all_messages[1:])

    await states.hide(opportunity_ids[0])
    message_count = len(telegram.messages)
    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/all"}})
    visible_messages = telegram.messages[message_count:]
    assert visible_messages[0][0] == "Все подходящие вакансии и проекты: 1"
    assert len(visible_messages) == 2


def test_all_command_is_registered_in_telegram_menu() -> None:
    assert ("all", "Показать все подходящие вакансии") in BOT_COMMANDS


@pytest.mark.asyncio
async def test_pause_and_resume_commands_are_persistent_and_idempotent(
    sqlite_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot, telegram, _, _ = await _bot(sqlite_session_factory)
    preferences = NotificationPreferenceService(sqlite_session_factory)

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/pause"}})
    assert await preferences.is_paused(BOHDAN_PROFILE.profile_id, "telegram") is True
    assert telegram.messages[-1][0].startswith("Автоматические уведомления приостановлены.")

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/pause"}})
    assert telegram.messages[-1][0] == "Автоматические уведомления уже приостановлены."

    await bot.handle_update({"message": {"chat": {"id": 123}, "text": "/resume"}})
    assert await preferences.is_paused(BOHDAN_PROFILE.profile_id, "telegram") is False
    assert (
        "Накопленные за время паузы предложения отправлены не будут" in (telegram.messages[-1][0])
    )


def test_pause_commands_are_registered_in_telegram_menu() -> None:
    assert ("pause", "Приостановить новые уведомления") in BOT_COMMANDS
    assert ("resume", "Возобновить новые уведомления") in BOT_COMMANDS
