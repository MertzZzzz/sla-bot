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


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("SOCKS5H://proxy.local:1080", "socks5://proxy.local:1080"),
        ("socks4a://10.0.0.1:1080/", "socks4://10.0.0.1:1080"),
        ("http://proxy.local:3128", "http://proxy.local:3128"),
        ("socks5://user:p%40ss@10.0.0.1:1080", "socks5://user:p%40ss@10.0.0.1:1080"),
    ],
)
def test_proxy_url_normalization(raw: str, normalized: str) -> None:
    settings = make_settings(proxy_url=raw)
    assert settings.telegram.proxy_url is not None
    assert settings.telegram.proxy_url.get_secret_value() == normalized


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://proxy:21",
        "https://proxy:443",
        "socks5://proxy",
        "socks5://:1080",
        "socks5://proxy:notaport",
        "http://proxy:3128/path",
        "proxy:1080",
    ],
)
def test_proxy_url_invalid(raw: str) -> None:
    with pytest.raises(ValidationError, match=r"[Pp]roxy"):
        make_settings(proxy_url=raw)


def test_proxy_disabled_by_default_and_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().telegram.proxy_url is None
    monkeypatch.setenv("APP_TELEGRAM__BOT_TOKEN", "1:secret")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_URL", "")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_USERNAME", "")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_PASSWORD", "")
    settings = Settings(_env_file=None)
    assert settings.telegram.proxy_url is None
    assert settings.telegram.proxy_username is None


def test_proxy_from_env_with_separate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TELEGRAM__BOT_TOKEN", "1:secret")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_URL", "socks5://10.0.0.1:1080")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_USERNAME", "bot")
    monkeypatch.setenv("APP_TELEGRAM__PROXY_PASSWORD", "s3cr:et@")
    tg = Settings(_env_file=None).telegram
    assert tg.proxy_username == "bot"
    assert tg.proxy_password is not None
    assert tg.proxy_password.get_secret_value() == "s3cr:et@"


def test_proxy_credentials_validation() -> None:
    with pytest.raises(ValidationError, match="require APP_TELEGRAM__PROXY_URL"):
        make_settings(proxy_username="u", proxy_password="p")
    with pytest.raises(ValidationError, match="both"):
        make_settings(proxy_url="socks5://h:1", proxy_username="u")


def test_proxy_secrets_hidden() -> None:
    settings = make_settings(
        proxy_url="socks5://user:url-secret@10.0.0.1:1080", proxy_password="pw", proxy_username="u"
    )
    dumped = repr(settings) + str(settings.model_dump())
    assert "url-secret" not in dumped
    assert "'pw'" not in dumped
    assert settings.telegram.proxy_display == "socks5://10.0.0.1:1080"


def test_admin_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_TELEGRAM__BOT_TOKEN", "1:secret")
    monkeypatch.setenv("APP_TELEGRAM__ADMIN_CHAT_IDS", "-1001111, -2222")
    settings = Settings(_env_file=None)
    assert settings.telegram.admin_chat_ids == frozenset({-1001111, -2222})
    assert settings.is_settings_chat(-1001111, "supergroup")
    assert settings.is_settings_chat(42, "private")
    assert not settings.is_settings_chat(-1009999, "supergroup")
    with pytest.raises(ValidationError):
        make_settings(admin_chat_ids="0")
