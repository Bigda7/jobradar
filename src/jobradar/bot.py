import asyncio
import signal
from html import escape
from typing import Any

import structlog

from jobradar.config import get_settings
from jobradar.db.session import engine, session_factory
from jobradar.domain.enums import OpportunityDisposition
from jobradar.logging_config import configure_logging
from jobradar.matching.profile import BOHDAN_PROFILE
from jobradar.notifications.currency import (
    CurrencyConversionError,
    ExchangeRateProvider,
    ExchangeRates,
    NbuExchangeRateClient,
)
from jobradar.notifications.messages import TelegramMessageRegistry
from jobradar.notifications.preferences import NotificationPreferenceService
from jobradar.notifications.service import (
    NotificationCandidate,
    NotificationService,
    format_match_message,
    opportunity_keyboard,
)
from jobradar.notifications.telegram import TelegramClient, TelegramDeliveryError
from jobradar.opportunities.service import OpportunityStateService

logger = structlog.get_logger(__name__)

BOT_COMMANDS = (
    ("latest", "Показать свежие подходящие вакансии"),
    ("all", "Показать все подходящие вакансии"),
    ("favorites", "Показать избранные вакансии"),
    ("stats", "Показать статистику агрегатора"),
    ("clear", "Удалить из чата сообщения с вакансиями"),
    ("pause", "Приостановить новые уведомления"),
    ("resume", "Возобновить новые уведомления"),
)


class TelegramBotService:
    def __init__(
        self,
        telegram_client: TelegramClient,
        notification_service: NotificationService,
        state_service: OpportunityStateService,
        message_registry: TelegramMessageRegistry,
        notification_preferences: NotificationPreferenceService,
        exchange_rate_provider: ExchangeRateProvider,
        allowed_chat_id: int,
        minimum_score: int,
        latest_limit: int,
        poll_timeout_seconds: int,
    ) -> None:
        self._telegram = telegram_client
        self._notifications = notification_service
        self._states = state_service
        self._messages = message_registry
        self._notification_preferences = notification_preferences
        self._exchange_rates = exchange_rate_provider
        self._allowed_chat_id = allowed_chat_id
        self._minimum_score = minimum_score
        self._latest_limit = latest_limit
        self._poll_timeout_seconds = poll_timeout_seconds

    async def run(self, stop_event: asyncio.Event) -> None:
        await self._telegram.set_my_commands(BOT_COMMANDS)
        offset: int | None = None
        logger.info("telegram_bot_polling_started", chat_id=self._allowed_chat_id)
        while not stop_event.is_set():
            try:
                updates = await self._telegram.get_updates(offset, self._poll_timeout_seconds)
            except TelegramDeliveryError as error:
                logger.warning("telegram_polling_failed", error=str(error))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2)
                except TimeoutError:
                    continue
                break

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)
                try:
                    await self.handle_update(update)
                except TelegramDeliveryError as error:
                    logger.warning(
                        "telegram_update_delivery_failed",
                        update_id=update_id,
                        error=str(error),
                    )
                except Exception as error:
                    logger.exception(
                        "telegram_update_failed",
                        update_id=update_id,
                        error=str(error),
                    )

    async def handle_update(self, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._handle_callback(callback)
            return
        message = update.get("message")
        if isinstance(message, dict):
            await self._handle_message(message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        chat_id = _message_chat_id(message)
        if chat_id != self._allowed_chat_id:
            logger.warning("telegram_unauthorized_message", chat_id=chat_id)
            return
        text = message.get("text")
        if not isinstance(text, str):
            return
        command = text.strip().split(maxsplit=1)[0].split("@", maxsplit=1)[0].casefold()
        if command == "/latest":
            await self._send_latest()
        elif command == "/all":
            await self._send_all()
        elif command == "/favorites":
            await self._send_favorites()
        elif command == "/stats":
            await self._send_stats()
        elif command == "/clear":
            await self._clear_opportunity_messages()
        elif command == "/pause":
            await self._pause_notifications()
        elif command == "/resume":
            await self._resume_notifications()
        else:
            await self._send_help()

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback.get("id")
        if not isinstance(callback_id, str):
            return
        message = callback.get("message")
        if not isinstance(message, dict):
            await self._telegram.answer_callback_query(callback_id, "Сообщение недоступно.")
            return
        chat_id = _message_chat_id(message)
        if chat_id != self._allowed_chat_id:
            await self._telegram.answer_callback_query(callback_id, "Нет доступа.")
            logger.warning("telegram_unauthorized_callback", chat_id=chat_id)
            return
        message_id = message.get("message_id")
        data = callback.get("data")
        if not isinstance(message_id, int) or not isinstance(data, str):
            await self._telegram.answer_callback_query(callback_id, "Некорректная команда.")
            return
        action, opportunity_id = _parse_callback_data(data)
        if action is None or opportunity_id is None:
            await self._telegram.answer_callback_query(callback_id, "Неизвестное действие.")
            return

        source_url = await self._states.source_url(opportunity_id)
        if source_url is None:
            await self._telegram.answer_callback_query(callback_id, "Вакансия не найдена.")
            return
        if action == "favorite":
            disposition = await self._states.toggle_favorite(opportunity_id)
            if disposition is None:
                await self._telegram.answer_callback_query(callback_id, "Вакансия не найдена.")
                return
            is_favorite = disposition is OpportunityDisposition.FAVORITE
            await self._telegram.edit_message_reply_markup(
                chat_id,
                message_id,
                opportunity_keyboard(
                    opportunity_id,
                    source_url,
                    is_favorite=is_favorite,
                ),
            )
            answer = "Добавлено в избранное." if is_favorite else "Удалено из избранного."
            await self._telegram.answer_callback_query(callback_id, answer)
            return

        if action == "restore":
            disposition = await self._states.restore(opportunity_id)
            if disposition is None:
                await self._telegram.answer_callback_query(callback_id, "Вакансия не найдена.")
                return
            await self._telegram.edit_message_reply_markup(
                chat_id,
                message_id,
                opportunity_keyboard(
                    opportunity_id,
                    source_url,
                    is_favorite=disposition is OpportunityDisposition.FAVORITE,
                ),
            )
            await self._telegram.answer_callback_query(
                callback_id,
                "Вакансия восстановлена.",
            )
            return

        hidden = await self._states.hide(opportunity_id)
        if not hidden:
            await self._telegram.answer_callback_query(callback_id, "Вакансия не найдена.")
            return
        await self._telegram.edit_message_reply_markup(
            chat_id,
            message_id,
            opportunity_keyboard(opportunity_id, source_url, is_hidden=True),
        )
        await self._telegram.answer_callback_query(
            callback_id,
            "Скрыто. Больше не буду показывать эту вакансию.",
        )

    async def _send_latest(self) -> None:
        candidates = await self._notifications.load_candidates(
            BOHDAN_PROFILE,
            self._minimum_score,
            latest_first=True,
            limit=self._latest_limit,
        )
        await self._send_candidates(
            candidates,
            empty_message="Новых подходящих вакансий пока нет.",
            heading="Последние подходящие вакансии и проекты",
        )

    async def _send_all(self) -> None:
        candidates = await self._notifications.load_candidates(
            BOHDAN_PROFILE,
            self._minimum_score,
            latest_first=True,
        )
        await self._send_candidates(
            candidates,
            empty_message="Подходящих вакансий и проектов пока нет.",
            heading="Все подходящие вакансии и проекты",
        )

    async def _send_candidates(
        self,
        candidates: list[NotificationCandidate],
        *,
        empty_message: str,
        heading: str,
    ) -> None:
        if not candidates:
            await self._telegram.send_message(empty_message)
            return
        rates_available, rates = await self._rates_for(candidates)
        if not rates_available:
            return
        favorite_ids = await self._states.favorite_ids()
        await self._telegram.send_message(f"{heading}: {len(candidates)}")
        for candidate in candidates:
            message_id = await self._telegram.send_message(
                format_match_message(candidate, rates),
                reply_markup=opportunity_keyboard(
                    candidate.opportunity_id,
                    candidate.source_url,
                    is_favorite=candidate.opportunity_id in favorite_ids,
                ),
            )
            await self._messages.record(candidate.opportunity_id, message_id)

    async def _send_favorites(self) -> None:
        favorites = await self._states.list_favorites(BOHDAN_PROFILE)
        if not favorites:
            await self._telegram.send_message("В избранном пока ничего нет.")
            return
        lines = [f"<b>Избранное: {len(favorites)}</b>"]
        for index, item in enumerate(favorites, start=1):
            title = escape(item.title)
            company = f" — {escape(item.company)}" if item.company else ""
            score = f", оценка {item.score}/100" if item.score is not None else ""
            if item.source_url:
                title = f'<a href="{escape(item.source_url, quote=True)}">{title}</a>'
            lines.append(f"{index}. {title}{company}{score}")
        await self._send_lines(lines)

    async def _send_stats(self) -> None:
        stats = await self._states.stats(BOHDAN_PROFILE, self._minimum_score)
        message = "\n".join(
            (
                "<b>Статистика JobRadar</b>",
                f"Собрано возможностей: {stats.collected}",
                f"Оценено текущими правилами: {stats.evaluated}",
                f"Подходят по порогу: {stats.matched}",
                f"Отфильтровано: {stats.filtered}",
                f"В избранном: {stats.favorites}",
                f"Скрыто: {stats.hidden}",
            )
        )
        await self._telegram.send_message(message)

    async def _clear_opportunity_messages(self) -> None:
        message_ids = await self._messages.active_message_ids()
        if not message_ids:
            await self._telegram.send_message("В чате нет учтённых сообщений с вакансиями.")
            return

        deleted_ids: list[int] = []
        failed = 0
        for message_id in message_ids:
            try:
                await self._telegram.delete_message(self._allowed_chat_id, message_id)
            except TelegramDeliveryError as error:
                if _message_is_already_deleted(error):
                    deleted_ids.append(message_id)
                    continue
                failed += 1
                logger.warning(
                    "telegram_opportunity_message_delete_failed",
                    message_id=message_id,
                    error=str(error),
                )
            else:
                deleted_ids.append(message_id)

        await self._messages.mark_deleted(deleted_ids)
        lines = [f"Удалено сообщений с вакансиями: {len(deleted_ids)}."]
        if failed:
            lines.append(f"Не удалось удалить: {failed}.")
        await self._telegram.send_message("\n".join(lines))

    async def _pause_notifications(self) -> None:
        result = await self._notification_preferences.set_paused(
            BOHDAN_PROFILE.profile_id,
            "telegram",
            True,
        )
        if result.changed:
            message = (
                "Автоматические уведомления приостановлены. Сбор и оценка вакансий продолжаются."
            )
        else:
            message = "Автоматические уведомления уже приостановлены."
        await self._telegram.send_message(message)

    async def _resume_notifications(self) -> None:
        result = await self._notification_preferences.set_paused(
            BOHDAN_PROFILE.profile_id,
            "telegram",
            False,
        )
        if result.changed:
            message = (
                "Автоматические уведомления возобновлены. "
                "Накопленные за время паузы предложения отправлены не будут; "
                "их можно посмотреть командой /all."
            )
        else:
            message = "Автоматические уведомления уже включены."
        await self._telegram.send_message(message)

    async def _send_help(self) -> None:
        await self._telegram.send_message(
            "\n".join(
                (
                    "<b>JobRadar</b>",
                    "Управление подходящими вакансиями и фриланс-проектами.",
                    "",
                    "/latest — последние подходящие предложения",
                    "/all — все подходящие предложения без лимита",
                    "/favorites — избранные предложения",
                    "/stats — статистика агрегатора",
                    "/clear — удалить сообщения с вакансиями из чата",
                    "/pause — приостановить автоматические уведомления",
                    "/resume — возобновить уведомления без отправки накопленного",
                )
            )
        )

    async def _rates_for(
        self,
        candidates: list[NotificationCandidate],
    ) -> tuple[bool, ExchangeRates | None]:
        if not any(
            item.salary_min is not None or item.salary_max is not None for item in candidates
        ):
            return True, None
        try:
            return True, await self._exchange_rates.fetch_rates()
        except CurrencyConversionError as error:
            logger.warning("telegram_command_currency_failed", error=str(error))
            await self._telegram.send_message(
                "Не удалось получить курсы валют. Попробуйте команду ещё раз позже."
            )
            return False, None

    async def _send_lines(self, lines: list[str]) -> None:
        chunk: list[str] = []
        chunk_length = 0
        for line in lines:
            line_length = len(line) + 1
            if chunk and chunk_length + line_length > 3500:
                await self._telegram.send_message("\n".join(chunk))
                chunk = []
                chunk_length = 0
            chunk.append(line)
            chunk_length += line_length
        if chunk:
            await self._telegram.send_message("\n".join(chunk))


def _message_chat_id(message: dict[str, Any]) -> int | None:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return chat_id if isinstance(chat_id, int) else None


def _parse_callback_data(value: str) -> tuple[str | None, int | None]:
    action, separator, raw_id = value.partition(":")
    if not separator or action not in {"favorite", "hide", "restore"}:
        return None, None
    try:
        opportunity_id = int(raw_id)
    except ValueError:
        return None, None
    if opportunity_id <= 0:
        return None, None
    return action, opportunity_id


def _message_is_already_deleted(error: TelegramDeliveryError) -> bool:
    error_text = str(error).casefold()
    return "message to delete not found" in error_text


async def run_bot() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass

    if not settings.telegram_enabled or not settings.telegram_polling_enabled:
        logger.info("telegram_bot_polling_disabled")
        await stop_event.wait()
        return
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise RuntimeError("Telegram polling is enabled without complete credentials.")

    telegram = TelegramClient(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_chat_id,
        request_timeout_seconds=settings.telegram_request_timeout_seconds,
    )
    exchange_rates = NbuExchangeRateClient(
        rates_url=settings.nbu_rates_url,
        request_timeout_seconds=settings.nbu_request_timeout_seconds,
    )
    service = TelegramBotService(
        telegram_client=telegram,
        notification_service=NotificationService(session_factory, telegram, exchange_rates),
        state_service=OpportunityStateService(session_factory),
        message_registry=TelegramMessageRegistry(session_factory),
        notification_preferences=NotificationPreferenceService(session_factory),
        exchange_rate_provider=exchange_rates,
        allowed_chat_id=settings.telegram_chat_id,
        minimum_score=settings.matching_min_score,
        latest_limit=settings.telegram_latest_limit,
        poll_timeout_seconds=settings.telegram_poll_timeout_seconds,
    )
    try:
        await service.run(stop_event)
    finally:
        await engine.dispose()
        logger.info("telegram_bot_stopped")


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
