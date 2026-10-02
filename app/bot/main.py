"""Bot entrypoint: ``python -m app.bot`` (polling or webhook, see APP_TELEGRAM__WEBHOOK_ENABLED)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from redis.asyncio import Redis

from app.bot.dispatcher import build_dispatcher
from app.bot.sender import build_bot
from app.bot.services import build_bot_services
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import build_async_engine, build_async_sessionmaker
from app.web.health import setup_health

logger = logging.getLogger(__name__)


async def _start_http(app: web.Application, settings: Settings) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, settings.app.http_host, settings.app.http_port).start()
    return runner


async def _run_polling(bot: Bot, dp: Dispatcher, app: web.Application, settings: Settings) -> None:
    runner = await _start_http(app, settings)
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await runner.cleanup()


async def _run_webhook(bot: Bot, dp: Dispatcher, app: web.Application, settings: Settings) -> None:
    tg = settings.telegram
    secret = tg.webhook_secret_token.get_secret_value() if tg.webhook_secret_token else None
    # SimpleRequestHandler rejects requests without a matching
    # X-Telegram-Bot-Api-Secret-Token header.
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=tg.webhook_path
    )
    setup_application(app, dp, bot=bot)

    async def on_startup(_: web.Application) -> None:
        await bot.set_webhook(
            url=tg.webhook_url,
            secret_token=secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("webhook registered", extra={"event": "webhook_registered"})

    app.on_startup.append(on_startup)
    runner = await _start_http(app, settings)
    logger.info("webhook server started", extra={"event": "webhook_started"})
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.logging)
    engine = build_async_engine(settings.database)
    redis = Redis(
        host=settings.redis.host,
        port=settings.redis.port,
        password=settings.redis.password.get_secret_value() if settings.redis.password else None,
        socket_timeout=2,
        socket_connect_timeout=2,
    )
    services = build_bot_services(settings, build_async_sessionmaker(engine))
    dp = build_dispatcher(settings, services)
    bot = build_bot(settings.telegram)
    app = web.Application()
    setup_health(app, engine, redis)
    mode = "webhook" if settings.telegram.webhook_enabled else "polling"
    logger.info("bot starting", extra={"event": f"bot_start_{mode}"})
    try:
        if settings.telegram.webhook_enabled:
            await _run_webhook(bot, dp, app, settings)
        else:
            await _run_polling(bot, dp, app, settings)
    finally:
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


def run() -> None:
    asyncio.run(main())
