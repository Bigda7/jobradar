from collections.abc import Sequence
from typing import Any, cast

import httpx


class TelegramDeliveryError(RuntimeError):
    pass


type InlineKeyboardMarkup = dict[str, list[list[dict[str, str]]]]


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        request_timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._request_timeout_seconds = request_timeout_seconds
        self._client = client
        self._api_base_url = api_base_url.rstrip("/")

    async def get_me(self) -> dict[str, Any]:
        payload = await self._call("getMe", {})
        result = payload.get("result")
        if not isinstance(result, dict):
            raise TelegramDeliveryError("Telegram getMe returned an invalid result.")
        return result

    async def send_message(
        self,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        chat_id: int | None = None,
    ) -> int:
        request: dict[str, Any] = {
            "chat_id": chat_id if chat_id is not None else self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup is not None:
            request["reply_markup"] = reply_markup
        payload = await self._call(
            "sendMessage",
            request,
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramDeliveryError("Telegram sendMessage returned an invalid result.")
        return cast(int, result["message_id"])

    async def get_updates(
        self,
        offset: int | None,
        timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        request: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            request["offset"] = offset
        payload = await self._call("getUpdates", request)
        result = payload.get("result")
        if not isinstance(result, list):
            raise TelegramDeliveryError("Telegram getUpdates returned an invalid result.")
        return [item for item in result if isinstance(item, dict)]

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id, "text": text},
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            },
        )

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self._call(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
            },
        )

    async def set_my_commands(self, commands: Sequence[tuple[str, str]]) -> None:
        command_payload = [
            {"command": command, "description": description} for command, description in commands
        ]
        await self._call("setMyCommands", {"commands": command_payload})
        await self._call(
            "setMyCommands",
            {
                "commands": command_payload,
                "language_code": "ru",
            },
        )

    async def _call(self, method: str, json_payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            return await self._request(self._client, method, json_payload)

        timeout = httpx.Timeout(self._request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._request(client, method, json_payload)

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        json_payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self._api_base_url}/bot{self._bot_token}/{method}"
        try:
            response = await client.post(url, json=json_payload)
        except httpx.HTTPError:
            raise TelegramDeliveryError("Telegram request failed.") from None

        try:
            payload = response.json()
        except ValueError:
            raise TelegramDeliveryError("Telegram returned a non-JSON response.") from None
        if not isinstance(payload, dict):
            raise TelegramDeliveryError("Telegram returned an invalid response.")
        if response.is_error or payload.get("ok") is not True:
            description = payload.get("description")
            safe_description = (
                str(description)[:500] if description else f"HTTP status {response.status_code}"
            )
            raise TelegramDeliveryError(f"Telegram rejected the request: {safe_description}")
        return payload
