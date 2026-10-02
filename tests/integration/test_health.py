from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.web.health import setup_health

pytestmark = pytest.mark.integration


class FakeRedis:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis down")
        return True


@pytest.fixture
async def engine(database_url: URL) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(database_url.set(drivername="postgresql+asyncpg"))
    yield eng
    await eng.dispose()


async def client_for(
    engine: AsyncEngine, redis: FakeRedis
) -> TestClient[web.Request, web.Application]:
    app = web.Application()
    setup_health(app, engine, redis)  # type: ignore[arg-type]
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_live_and_ready(engine: AsyncEngine) -> None:
    client = await client_for(engine, FakeRedis(healthy=True))
    try:
        live = await client.get("/health/live")
        assert live.status == 200
        ready = await client.get("/health/ready")
        assert ready.status == 200
        assert (await ready.json())["checks"] == {"postgres": "ok", "redis": "ok"}
    finally:
        await client.close()


async def test_ready_fails_when_dependency_down(engine: AsyncEngine) -> None:
    client = await client_for(engine, FakeRedis(healthy=False))
    try:
        ready = await client.get("/health/ready")
        assert ready.status == 503
        assert (await ready.json())["checks"]["redis"] == "error"
    finally:
        await client.close()
