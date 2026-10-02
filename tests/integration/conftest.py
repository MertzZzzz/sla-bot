"""Integration fixtures: a throw-away PostgreSQL database migrated with Alembic.

Point ``APP_TEST_DATABASE_URL`` at a server where the user may create databases,
e.g. ``postgresql+psycopg://postgres@127.0.0.1:5432/postgres``. Tests are skipped
when the server is unreachable.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices, build_bot_services
from app.core.config import CelerySettings, Settings, TelegramSettings
from app.db.uow import SyncUnitOfWork
from tests.factories import FakeClock

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql+psycopg://postgres@127.0.0.1:5432/postgres"
ADMIN_ID = 1000

pytestmark = pytest.mark.integration


def _server_url() -> URL:
    return make_url(os.environ.get("APP_TEST_DATABASE_URL", DEFAULT_URL))


@pytest.fixture(scope="session")
def database_url() -> Iterator[URL]:
    server = _server_url()
    name = f"sla_bot_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not available for integration tests: {exc}")
    url = server.set(database=name)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def sync_factory(database_url: URL) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(database_url, pool_size=10)
    yield sessionmaker(engine, expire_on_commit=False, autoflush=False)
    engine.dispose()


@pytest.fixture(scope="session")
async def async_factory(database_url: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url.set(drivername="postgresql+asyncpg"), pool_size=10)
    yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(sync_factory: sessionmaker[Session]) -> None:
    with sync_factory() as session:
        session.execute(
            text(
                "TRUNCATE outbox_events, reply_events, chat_configuration_audit, pending_replies,"
                " chat_responders, monitored_chats, telegram_users RESTART IDENTITY CASCADE"
            )
        )
        session.commit()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram=TelegramSettings(
            bot_token=SecretStr("1:test"),
            admin_telegram_ids=frozenset({ADMIN_ID}),
        ),
        celery=CelerySettings(retry_backoff_base_seconds=5, max_delivery_attempts=3),
    )


@pytest.fixture
def services(
    settings: Settings, async_factory: async_sessionmaker[AsyncSession], clock: FakeClock
) -> BotServices:
    return build_bot_services(settings, async_factory, clock)


@pytest.fixture
def sync_uow(sync_factory: sessionmaker[Session]) -> Callable[[], SyncUnitOfWork]:
    return lambda: SyncUnitOfWork(sync_factory)
