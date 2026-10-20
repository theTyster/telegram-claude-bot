"""Entry point: ``python -m bot.main``."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta

import aiohttp

from .config import ConfigError, load_config
from .core import FamilyBot
from .telegram_client import TelegramClient, TelegramError
from .transport import run_polling, run_webhook
from .usage import SqliteUsageStore, day_key

log = logging.getLogger("bot")


async def purge_old_usage(store: SqliteUsageStore, tz) -> None:
    """Keep only the last week of usage counts."""
    while True:
        cutoff = day_key(tz, datetime.now(tz) - timedelta(days=7))
        await store.purge_before(cutoff)
        await asyncio.sleep(6 * 3600)


async def start_with_retry(bot: FamilyBot) -> None:
    """Connect to Telegram, retrying network problems but not a bad token."""
    delay = 5
    while True:
        try:
            await bot.start()
            return
        except TelegramError as e:
            if e.code in (401, 404):
                raise ConfigError("Telegram rejected TELEGRAM_BOT_TOKEN; check it with @BotFather") from e
            log.warning("Telegram error at start-up (%s); retrying in %ss", e, delay)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Can't reach Telegram (%s); retrying in %ss", type(e).__name__, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 300)


async def amain() -> None:
    cfg = load_config()
    log.info(
        "Starting: auth=%s model=%s transport=%s limit=%s/day tz=%s",
        cfg.auth_mode,
        cfg.model,
        cfg.transport,
        cfg.daily_limit,
        cfg.timezone,
    )
    store = SqliteUsageStore(cfg.usage_db_path)
    tg = TelegramClient(cfg.telegram_bot_token)
    bot = FamilyBot(cfg, tg, store)
    await start_with_retry(bot)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    if cfg.transport == "webhook":
        receiver = run_webhook(
            bot,
            tg,
            host=cfg.webhook_host,
            port=cfg.webhook_port,
            secret=cfg.webhook_secret,
            public_url=os.environ.get("WEBHOOK_PUBLIC_URL") or None,
        )
    else:
        receiver = run_polling(bot, tg)

    tasks = [asyncio.create_task(receiver), asyncio.create_task(purge_old_usage(store, cfg.tz))]
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    log.info("Shutting down; letting running questions finish (up to 30s)")
    try:
        await asyncio.wait_for(bot.wait_idle(), timeout=30)
    except asyncio.TimeoutError:
        log.warning("Some questions were still running at shutdown")
    await tg.close()
    store.close()
    for t in done:
        if t is not stopper and t.exception():
            raise t.exception()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # aiohttp's access log and request errors can include the bot token in URLs.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        asyncio.run(amain())
    except ConfigError as e:
        log.error("Configuration problem: %s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
