"""The bot's behavior, independent of how updates arrive (polling or webhook).

``handle_update`` returns quickly: questions run as background tasks so the
update loop keeps buffering forwarded messages that arrive right after a
command ("forward with comment" sends the comment first).
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

from .agent import AgentResult, ImageInput, run_agent
from .config import Config
from .context import ForwardBuffer, build_prompt, has_content, select_photos
from .gate import MAX_IMAGE_BYTES, BotIdentity, Decision, Invocation, classify
from .telegram_client import TelegramClient, TelegramError
from .telegram_fmt import md_to_html, split_markdown, strip_markdown
from .usage import UsageStore, day_key, next_reset

log = logging.getLogger(__name__)

COMMANDS = [
    ("claude", "Ask Claude something"),
    ("usage", "How many questions you have left today"),
    ("help", "How to use this bot"),
]


def help_text(bot_username: str, daily_limit: int) -> str:
    return (
        "<b>Ask me anything</b> 👋\n\n"
        f"• <code>/claude your question</code>\n"
        f"• mention me: <code>@{bot_username} your question</code>\n"
        "• reply to one of my messages to follow up\n\n"
        "Reply to someone's message (or a photo) while asking, and I'll see it too. "
        "You can also forward a few messages here and then ask me about them.\n\n"
        "I can search the web, look at photos and send pictures. "
        f"Everyone gets {daily_limit} questions a day; see /usage."
    )


class FamilyBot:
    def __init__(
        self,
        cfg: Config,
        tg: TelegramClient,
        usage: UsageStore,
        *,
        agent_runner=run_agent,
        clock=None,
    ):
        self.cfg = cfg
        self.tg = tg
        self.usage = usage
        self.agent_runner = agent_runner
        self.identity: BotIdentity | None = None
        buffer_kwargs = {"clock": clock} if clock else {}
        self.forwards = ForwardBuffer(
            cfg.forward_buffer_seconds, cfg.forward_buffer_max_messages, **buffer_kwargs
        )
        self._semaphore = asyncio.Semaphore(cfg.max_concurrent_calls)
        self._busy_users: set[int] = set()
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        me = await self.tg.get_me()
        self.identity = BotIdentity(me["id"], me["username"])
        log.info("Running as @%s", self.identity.username)
        try:
            await self.tg.set_my_commands(COMMANDS)
        except TelegramError as e:
            log.warning("Could not set the command list: %s", e)

    def _fmt_date(self, unix: int) -> str:
        return datetime.fromtimestamp(unix, self.cfg.tz).strftime("%b %d, %H:%M")

    def _is_owner(self, user_id: int | None) -> bool:
        return user_id == self.cfg.owner_user_id

    async def wait_idle(self) -> None:
        """Wait for all running questions (used by tests and on shutdown)."""
        while pending := [t for t in self._tasks if not t.done()]:
            await asyncio.wait(pending)

    async def handle_update(self, update: dict) -> None:
        assert self.identity, "call start() first"
        decision = classify(
            update,
            bot=self.identity,
            family_chat_id=self.cfg.family_chat_id,
            owner_user_id=self.cfg.owner_user_id,
            fmt_date=self._fmt_date,
        )
        try:
            await self._dispatch(decision)
        except TelegramError as e:
            log.warning("Telegram error handling %s: %s", decision.kind, e)

    async def _dispatch(self, d: Decision) -> None:
        if d.kind == "ignore":
            return
        if d.kind == "forward":
            self.forwards.add(d.chat_id, d.user_id, d.forward)
            return
        if d.kind == "leave":
            log.info(
                "Leaving chat %s (not the family group; if it should be, set FAMILY_CHAT_ID to it)",
                d.chat_id,
            )
            await self.tg.leave_chat(d.chat_id)
            return
        if d.kind == "dm_refused":
            await self.tg.send_message(
                d.chat_id, "Sorry, I only answer in the family group chat. 🙂", html=False
            )
            return
        if d.kind == "help":
            await self.tg.send_message(
                d.chat_id,
                help_text(self.identity.username, self.cfg.daily_limit),
                reply_to=d.message_id,
            )
            return
        if d.kind == "usage":
            await self._reply_usage(d)
            return
        if d.kind == "invoke":
            await self._start_invocation(d.invocation)

    async def _reply_usage(self, d: Decision) -> None:
        if self._is_owner(d.user_id):
            text = "You're the owner, so you have no daily limit. 👑"
        else:
            used = await self.usage.used(d.user_id, day_key(self.cfg.tz))
            left = max(self.cfg.daily_limit - used, 0)
            text = f"You have {left} of {self.cfg.daily_limit} questions left today."
            if left == 0:
                text += f" They reset at {self._reset_text()}."
        await self.tg.send_message(d.chat_id, text, reply_to=d.message_id, html=False)

    def _reset_text(self) -> str:
        return next_reset(self.cfg.tz).strftime("midnight (%Z)")

    async def _start_invocation(self, inv: Invocation) -> None:
        if inv.user_id in self._busy_users:
            await self.tg.send_message(
                inv.chat_id,
                "I'm still working on your last question. One at a time, please! ⏳",
                reply_to=inv.message_id,
                html=False,
            )
            return

        day = day_key(self.cfg.tz)
        left: int | None = None
        if not self._is_owner(inv.user_id):
            left = await self.usage.try_consume(inv.user_id, day, self.cfg.daily_limit)
            if left is None:
                await self.tg.send_message(
                    inv.chat_id,
                    f"You've used all {self.cfg.daily_limit} of your questions for today. "
                    f"They reset at {self._reset_text()}. 🌙",
                    reply_to=inv.message_id,
                    html=False,
                )
                return

        self._busy_users.add(inv.user_id)
        task = asyncio.create_task(self._run(inv, day, left))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, inv: Invocation, day: str, left: int | None) -> None:
        placeholder: dict | None = None
        typing = None
        succeeded = False
        try:
            placeholder = await self.tg.send_message(
                inv.chat_id, "🔎 Thinking…", reply_to=inv.message_id, html=False
            )
            typing = asyncio.create_task(self._keep_typing(inv.chat_id))

            # Catch forwards that arrive just after the command.
            await asyncio.sleep(self.cfg.forward_grace_seconds)
            forwards = self.forwards.take(inv.chat_id, inv.user_id)

            if not has_content(inv, forwards):
                await self._finish(
                    inv,
                    placeholder,
                    "What would you like to know? Try <code>/claude your question</code>, "
                    "or reply to a message while asking.",
                    html_ready=True,
                )
                return  # not succeeded, so the call is refunded below

            images = await self._download_images(select_photos(inv, forwards, self.cfg.max_images))
            prompt = build_prompt(
                inv,
                forwards,
                now_text=datetime.now(self.cfg.tz).strftime("%A, %B %d, %Y %H:%M %Z"),
            )
            async with self._semaphore:
                result: AgentResult = await asyncio.wait_for(
                    self.agent_runner(self.cfg, prompt, images),
                    timeout=self.cfg.call_timeout_seconds,
                )
            log.info(
                "Answered user %s in %s turns, cost %s",
                inv.user_id,
                result.num_turns,
                f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a",
            )

            text = result.text
            if left is not None:
                text += f"\n\n_({left} of {self.cfg.daily_limit} questions left today)_"
            await self._finish(inv, placeholder, text)
            await self._send_images(inv, result.image_urls)
            succeeded = True
        except asyncio.TimeoutError:
            log.warning("Question from user %s timed out", inv.user_id)
            await self._finish_error(
                inv, placeholder, "That took too long, so I gave up. It didn't count against your limit."
            )
        except Exception:
            log.exception("Question from user %s failed", inv.user_id)
            await self._finish_error(
                inv,
                placeholder,
                "Sorry, something went wrong. It didn't count against your limit; please try again.",
            )
        finally:
            if typing:
                typing.cancel()
            self._busy_users.discard(inv.user_id)
            if not succeeded and left is not None:
                await self.usage.refund(inv.user_id, day)

    async def _keep_typing(self, chat_id: int) -> None:
        try:
            while True:
                try:
                    await self.tg.send_chat_action(chat_id)
                except TelegramError:
                    pass
                await asyncio.sleep(4.5)
        except asyncio.CancelledError:
            pass

    async def _download_images(self, photos) -> list[ImageInput]:
        images = []
        for photo in photos:
            try:
                data = await self.tg.download_file(photo.file_id, max_bytes=MAX_IMAGE_BYTES)
                images.append(ImageInput(photo.mime_type, data))
            except Exception as e:
                log.warning("Skipping a photo that could not be downloaded: %s", e)
        return images

    async def _finish(
        self, inv: Invocation, placeholder: dict | None, text: str, *, html_ready: bool = False
    ) -> None:
        """Replace the placeholder with the answer, sending extra chunks as replies."""
        chunks = [text] if html_ready else split_markdown(text)
        for i, chunk in enumerate(chunks):
            body = chunk if html_ready else md_to_html(chunk)
            try:
                await self._post(inv, placeholder if i == 0 else None, body, html=True)
            except TelegramError as e:
                if "parse" not in e.description.lower() and "entit" not in e.description.lower():
                    raise
                log.info("Telegram rejected the HTML, sending plain text instead")
                plain = chunk if html_ready else strip_markdown(chunk)
                await self._post(inv, placeholder if i == 0 else None, plain, html=False)

    async def _post(self, inv: Invocation, placeholder: dict | None, text: str, *, html: bool):
        if placeholder:
            try:
                await self.tg.edit_message(inv.chat_id, placeholder["message_id"], text, html=html)
                return
            except TelegramError as e:
                if "parse" in e.description.lower() or "entit" in e.description.lower():
                    raise
                log.info("Could not edit the placeholder (%s); sending a new message", e)
        await self.tg.send_message(inv.chat_id, text, reply_to=inv.message_id, html=html)

    async def _finish_error(self, inv: Invocation, placeholder: dict | None, text: str) -> None:
        try:
            await self._post(inv, placeholder, f"😕 {text}", html=False)
        except TelegramError as e:
            log.warning("Could not send the error message: %s", e)

    async def _send_images(self, inv: Invocation, urls: list[str]) -> None:
        if not urls:
            return
        if len(urls) > 1:
            try:
                await self.tg.send_media_group(inv.chat_id, urls, reply_to=inv.message_id)
                return
            except TelegramError as e:
                log.info("Album failed (%s); sending images one by one", e)
        failed = []
        for url in urls:
            try:
                await self.tg.send_photo(inv.chat_id, url, reply_to=inv.message_id)
            except TelegramError:
                failed.append(url)
        if failed:
            links = "\n".join(
                f'<a href="{html.escape(u, quote=True)}">Image {i}</a>'
                for i, u in enumerate(failed, 1)
            )
            try:
                await self.tg.send_message(
                    inv.chat_id,
                    "I couldn't attach some pictures, but here are the links:\n" + links,
                    reply_to=inv.message_id,
                )
            except TelegramError as e:
                log.warning("Could not send image links: %s", e)
