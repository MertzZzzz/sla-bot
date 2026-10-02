"""Liveness/readiness endpoints for orchestration."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0
ENGINE_KEY = web.AppKey("db_engine", AsyncEngine)
REDIS_KEY = web.AppKey("redis", Redis)


async def live(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _check_db(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis(redis: Redis) -> None:
    await redis.ping()


async def ready(request: web.Request) -> web.Response:
    checks = {
        "postgres": _check_db(request.app[ENGINE_KEY]),
        "redis": _check_redis(request.app[REDIS_KEY]),
    }
    results: dict[str, str] = {}
    for name, coro in checks.items():
        try:
            await asyncio.wait_for(coro, timeout=CHECK_TIMEOUT_SECONDS)
            results[name] = "ok"
        except Exception as exc:
            results[name] = "error"
            logger.warning(
                "readiness check failed",
                extra={"event": "readiness_failed", "error_type": type(exc).__name__},
            )
    healthy = all(v == "ok" for v in results.values())
    return web.json_response(
        {"status": "ok" if healthy else "unavailable", "checks": results},
        status=200 if healthy else 503,
    )


def setup_health(app: web.Application, engine: AsyncEngine, redis: Redis) -> None:
    app[ENGINE_KEY] = engine
    app[REDIS_KEY] = redis
    app.router.add_get("/health/live", live)
    app.router.add_get("/health/ready", ready)
