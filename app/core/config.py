"""Application settings (pydantic-settings v2).

Every variable uses the ``APP_`` prefix and ``__`` as nested delimiter, e.g.
``APP_TELEGRAM__BOT_TOKEN`` or ``APP_DATABASE__HOST``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

from app.core.enums import Priority, ReplyMatchMode
from app.core.types import TimezoneName


class AppSettings(BaseModel):
    environment: Literal["local", "test", "production"] = "local"
    default_timezone: TimezoneName = "Europe/Moscow"
    default_sla_seconds: int = Field(default=900, gt=0)
    default_priority: Priority = Priority.P3
    default_reply_match_mode: ReplyMatchMode = ReplyMatchMode.ANY_RESPONDER_MESSAGE
    # Responsible users may press "Ответ не требуется" for their own chats.
    responsible_can_mark_not_required: bool = True
    # Assigning a responsible also adds them to the chat responders.
    auto_add_responsible_as_responder: bool = True
    # One HTTP server serves /health/* and, in webhook mode, the Telegram webhook.
    http_host: str = "0.0.0.0"  # noqa: S104 - container listens on all interfaces
    http_port: int = Field(default=8080, gt=0, lt=65536)


class TelegramSettings(BaseModel):
    bot_token: SecretStr
    admin_telegram_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    webhook_enabled: bool = False
    webhook_base_url: str | None = None
    webhook_path: str = "/telegram/webhook"
    webhook_secret_token: SecretStr | None = None
    request_timeout_seconds: float = Field(default=15.0, gt=0)

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if value is None:
            return frozenset()
        if isinstance(value, int):
            return frozenset({value})
        if isinstance(value, str):
            raw = value.strip().strip("[]")
            parts = [p.strip() for p in raw.replace(";", ",").replace(" ", ",").split(",")]
            return frozenset(int(p) for p in parts if p)
        return value

    @field_validator("admin_telegram_ids")
    @classmethod
    def admin_ids_positive(cls, value: frozenset[int]) -> frozenset[int]:
        if any(v <= 0 for v in value):
            msg = "Admin Telegram IDs must be positive user IDs"
            raise ValueError(msg)
        return value

    @field_validator("webhook_path")
    @classmethod
    def path_starts_with_slash(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @model_validator(mode="after")
    def check_webhook(self) -> TelegramSettings:
        if self.webhook_enabled:
            if not self.webhook_base_url:
                msg = "APP_TELEGRAM__WEBHOOK_BASE_URL is required when webhook is enabled"
                raise ValueError(msg)
            if (
                self.webhook_secret_token is None
                or not self.webhook_secret_token.get_secret_value()
            ):
                msg = "APP_TELEGRAM__WEBHOOK_SECRET_TOKEN is required when webhook is enabled"
                raise ValueError(msg)
        return self

    @property
    def webhook_url(self) -> str:
        base = (self.webhook_base_url or "").rstrip("/")
        return f"{base}{self.webhook_path}"


class DatabaseSettings(BaseModel):
    host: str = "postgres"
    port: int = 5432
    name: str = "telegram_sla_bot"
    user: str = "telegram_sla_bot"
    password: SecretStr = SecretStr("")
    pool_size: int = Field(default=5, gt=0)
    max_overflow: int = Field(default=5, ge=0)
    echo: bool = False

    def _url(self, driver: str) -> URL:
        return URL.create(
            drivername=driver,
            username=self.user,
            password=self.password.get_secret_value() or None,
            host=self.host,
            port=self.port,
            database=self.name,
        )

    @property
    def async_url(self) -> URL:
        return self._url("postgresql+asyncpg")

    @property
    def sync_url(self) -> URL:
        return self._url("postgresql+psycopg")


class RedisSettings(BaseModel):
    host: str = "redis"
    port: int = 6379
    password: SecretStr | None = None
    broker_db: int = 0
    result_db: int = 1

    def url(self, db: int) -> str:
        auth = ""
        if self.password is not None and self.password.get_secret_value():
            auth = f":{quote(self.password.get_secret_value(), safe='')}@"
        return f"redis://{auth}{self.host}:{self.port}/{db}"

    @property
    def broker_url(self) -> str:
        return self.url(self.broker_db)

    @property
    def result_backend_url(self) -> str:
        return self.url(self.result_db)


class CelerySettings(BaseModel):
    scan_interval_seconds: float = Field(default=30.0, gt=0)
    delivery_sweep_interval_seconds: float = Field(default=30.0, gt=0)
    scan_batch_size: int = Field(default=100, gt=0, le=1000)
    delivery_batch_size: int = Field(default=50, gt=0, le=1000)
    max_delivery_attempts: int = Field(default=8, gt=0)
    retry_backoff_base_seconds: int = Field(default=5, gt=0)
    retry_backoff_max_seconds: int = Field(default=600, gt=0)
    # Outbox events stuck in ``processing`` longer than this are considered abandoned.
    processing_timeout_seconds: int = Field(default=300, gt=0)
    # If a worker died *during* the Telegram call we cannot know whether the message
    # was delivered. False = mark failed (no duplicates), True = resend (no losses).
    resend_stale_notifications: bool = False
    task_soft_time_limit_seconds: int = Field(default=60, gt=0)
    task_time_limit_seconds: int = Field(default=90, gt=0)


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_format: bool = True


_SETTINGS_CONFIG = SettingsConfigDict(
    env_prefix="APP_",
    env_nested_delimiter="__",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class DatabaseOnlySettings(BaseSettings):
    """Subset used by Alembic so migrations do not require the bot token."""

    model_config = _SETTINGS_CONFIG

    database: DatabaseSettings = DatabaseSettings()


class Settings(BaseSettings):
    model_config = _SETTINGS_CONFIG

    app: AppSettings = AppSettings()
    telegram: TelegramSettings
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    celery: CelerySettings = CelerySettings()
    logging: LoggingSettings = LoggingSettings()

    def is_global_admin(self, telegram_user_id: int | None) -> bool:
        return telegram_user_id is not None and telegram_user_id in self.telegram.admin_telegram_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
