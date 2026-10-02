from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import AppSettings, RedisSettings, Settings, TelegramSettings


def make_settings(**telegram: object) -> Settings:
    return Settings(telegram=TelegramSettings(bot_token=SecretStr("1:x"), **telegram))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,2,3", {1, 2, 3}),
        (" 10 , 20 ", {10, 20}),
        ("[5, 6]", {5, 6}),
        ("7;8", {7, 8}),
        ("", set()),
        (42, {42}),
    ],
)
def test_admin_ids_parsing(raw: object, expected: set[int]) -> None:
    settings = make_settings(admin_telegram_ids=raw)
    assert settings.telegram.admin_telegram_ids == frozenset(expected)


def test_admin_ids_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TELEGRAM__BOT_TOKEN", "1:secret")
    monkeypatch.setenv("APP_TELEGRAM__ADMIN_TELEGRAM_IDS", "111,222")
    monkeypatch.setenv("APP_DATABASE__PORT", "6543")
    monkeypatch.setenv("APP_CELERY__SCAN_INTERVAL_SECONDS", "10")
    settings = Settings(_env_file=None)
    assert settings.telegram.admin_telegram_ids == frozenset({111, 222})
    assert settings.database.port == 6543
    assert settings.celery.scan_interval_seconds == 10
    assert settings.is_global_admin(111)
    assert not settings.is_global_admin(333)
    assert not settings.is_global_admin(None)


@pytest.mark.parametrize("raw", ["abc", "1,-5", "0"])
def test_admin_ids_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(admin_telegram_ids=raw)


def test_webhook_requires_url_and_secret() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_BASE_URL"):
        make_settings(webhook_enabled=True, webhook_secret_token="s")
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET_TOKEN"):
        make_settings(webhook_enabled=True, webhook_base_url="https://example.com")
    ok = make_settings(
        webhook_enabled=True,
        webhook_base_url="https://example.com/",
        webhook_secret_token="s",
        webhook_path="hook",
    )
    assert ok.telegram.webhook_url == "https://example.com/hook"


def test_secrets_not_exposed() -> None:
    settings = make_settings()
    settings.database.password = SecretStr("db-pass")
    dumped = repr(settings) + str(settings.model_dump())
    assert "1:x" not in dumped
    assert "db-pass" not in dumped
    assert "db-pass" not in repr(settings.database.async_url)


def test_database_urls() -> None:
    settings = make_settings()
    assert settings.database.async_url.drivername == "postgresql+asyncpg"
    assert settings.database.sync_url.drivername == "postgresql+psycopg"


def test_redis_url_with_password() -> None:
    redis = RedisSettings(host="r", password=SecretStr("p@ss"))
    assert redis.broker_url == "redis://:p%40ss@r:6379/0"
    assert RedisSettings(host="r").result_backend_url == "redis://r:6379/1"


def test_timezone_validation() -> None:
    assert (
        AppSettings(default_timezone="Asia/Yekaterinburg").default_timezone == "Asia/Yekaterinburg"
    )
    with pytest.raises(ValidationError):
        AppSettings(default_timezone="Mars/Olympus")
