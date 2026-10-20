"""How updates reach ``FamilyBot.handle_update``.

- ``polling`` (default): long-poll ``getUpdates``. No public URL needed.
- ``webhook``: a small HTTP server for Telegram, or for a Cloudflare Worker
  forwarding Telegram's webhook into a Sandbox container, to POST to.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time

from aiohttp import web

from .core import FamilyBot
from .telegram_client import TelegramClient

log = logging.getLogger(__name__)

HEALTH_FILE = os.environ.get("HEALTH_FILE", "/tmp/bot-health")
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def touch_health() -> None:
    try:
        with open(HEALTH_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


async def run_polling(bot: FamilyBot, tg: TelegramClient) -> None:
    # Polling and a webhook can't both be active.
    await tg.delete_webhook()
    offset: int | None = None
    backoff = 1.0
    log.info("Polling Telegram for updates")
    while True:
        try:
            updates = await tg.get_updates(offset)
            touch_health()
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("getUpdates failed (%s); retrying in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                await bot.handle_update(update)
            except Exception:
                log.exception("Failed to handle update %s", update.get("update_id"))


def make_webhook_app(bot: FamilyBot, secret: str) -> web.Application:
    async def telegram(request: web.Request) -> web.Response:
        given = request.headers.get(SECRET_HEADER, "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            return web.Response(status=401)
        try:
            update = await request.json()
        except ValueError:
            return web.Response(status=400)
        try:
            await bot.handle_update(update)
        except Exception:
            log.exception("Failed to handle update %s", update.get("update_id"))
        # Always 200 so Telegram doesn't retry an update we've already seen.
        return web.Response(text="ok")

    async def healthz(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/telegram", telegram)
    app.router.add_get("/healthz", healthz)
    return app


async def run_webhook(
    bot: FamilyBot,
    tg: TelegramClient,
    *,
    host: str,
    port: int,
    secret: str,
    public_url: str | None,
) -> None:
    runner = web.AppRunner(make_webhook_app(bot, secret))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("Webhook server listening on %s:%s", host, port)
    if public_url:
        await tg.set_webhook(public_url, secret)
        log.info("Registered the webhook with Telegram")
    try:
        while True:
            touch_health()
            await asyncio.sleep(30)
    finally:
        await runner.cleanup()
