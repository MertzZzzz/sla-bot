from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

import app.db.models  # noqa: F401 - register models in metadata
from app.core.config import DatabaseOnlySettings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> URL | str:
    # Tests (or ops) may pass an explicit sync URL; otherwise use APP_DATABASE__*.
    explicit = config.attributes.get("sqlalchemy_url") or config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit  # type: ignore[no-any-return]
    return DatabaseOnlySettings().database.sync_url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url.render_as_string(hide_password=False) if isinstance(url, URL) else url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
