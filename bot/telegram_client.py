"""Minimal async client for the Telegram Bot API (just what the bot uses)."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    def __init__(self, method: str, code: int | None, description: str):
        super().__init__(f"{method}: {code} {description}")
        self.code = code
        self.description = description


class TelegramClient:
    def __init__(self, token: str, *, base_url: str = "https://api.telegram.org"):
        self._token = token
        self._base = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def call(self, method: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self._base}/bot{self._token}/{method}"
        for attempt in range(3):
            session = await self._http()
            async with session.post(url, json=params) as resp:
                data = await resp.json(content_type=None)
            if data.get("ok"):
                return data.get("result")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if data.get("error_code") == 429 and retry_after and attempt < 2:
                log.warning("Telegram rate limit on %s, waiting %ss", method, retry_after)
                await asyncio.sleep(retry_after)
                continue
            raise TelegramError(method, data.get("error_code"), data.get("description", ""))
        raise TelegramError(method, 429, "rate limited")

    # --- Bot API methods -------------------------------------------------

    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return await self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "my_chat_member"],
        )

    async def delete_webhook(self) -> None:
        await self.call("deleteWebhook")

    async def set_webhook(self, url: str, secret: str) -> None:
        await self.call(
            "setWebhook",
            url=url,
            secret_token=secret,
            allowed_updates=["message", "my_chat_member"],
        )

    async def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        await self.call(
            "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in commands],
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        html: bool = True,
    ) -> dict:
        return await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML" if html else None,
            link_preview_options={"is_disabled": True},
            reply_parameters=(
                {"message_id": reply_to, "allow_sending_without_reply": True}
                if reply_to
                else None
            ),
        )

    async def edit_message(self, chat_id: int, message_id: int, text: str, *, html: bool = True):
        return await self.call(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML" if html else None,
            link_preview_options={"is_disabled": True},
        )

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self.call("sendChatAction", chat_id=chat_id, action=action)

    async def send_photo(self, chat_id: int, url: str, *, reply_to: int | None = None) -> dict:
        return await self.call(
            "sendPhoto",
            chat_id=chat_id,
            photo=url,
            reply_parameters=(
                {"message_id": reply_to, "allow_sending_without_reply": True}
                if reply_to
                else None
            ),
        )

    async def send_media_group(
        self, chat_id: int, urls: list[str], *, reply_to: int | None = None
    ) -> list[dict]:
        return await self.call(
            "sendMediaGroup",
            chat_id=chat_id,
            media=[{"type": "photo", "media": u} for u in urls],
            reply_parameters=(
                {"message_id": reply_to, "allow_sending_without_reply": True}
                if reply_to
                else None
            ),
        )

    async def leave_chat(self, chat_id: int) -> None:
        await self.call("leaveChat", chat_id=chat_id)

    async def download_file(self, file_id: str, *, max_bytes: int) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        path = info.get("file_path")
        if not path:
            raise TelegramError("getFile", None, "no file_path")
        if (info.get("file_size") or 0) > max_bytes:
            raise TelegramError("getFile", None, "file too large")
        session = await self._http()
        async with session.get(f"{self._base}/file/bot{self._token}/{path}") as resp:
            # Not raise_for_status(): its message includes the URL, which holds the token.
            if resp.status != 200:
                raise TelegramError("downloadFile", resp.status, "download failed")
            data = await resp.content.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise TelegramError("getFile", None, "file too large")
        return data
