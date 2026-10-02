"""Validated arguments of admin commands."""

from __future__ import annotations

import re

from pydantic import Field, ValidationError

from app.core.types import SlaSeconds, TelegramChatId, ThreadId, TimezoneName
from app.schemas.common import Schema


class CommandArgumentError(ValueError):
    """Raised when command arguments are invalid; message is user-facing."""


def _split(args: str | None) -> list[str]:
    return (args or "").split()


_DURATION_UNITS: dict[str, int] = {
    "d": 86400,
    "д": 86400,
    "дн": 86400,
    "h": 3600,
    "ч": 3600,
    "m": 60,
    "м": 60,
    "мин": 60,
    "min": 60,
    "s": 1,
    "с": 1,
    "сек": 1,
    "sec": 1,
}
_DURATION_TOKEN = re.compile(r"(\d+)\s*([a-zа-я]*)", re.IGNORECASE)


class SlaInput(Schema):
    """Free-form SLA: ``45`` (minutes), ``1h30m``, ``2ч``, ``90s``, ``1d``."""

    sla_seconds: SlaSeconds

    @classmethod
    def parse(cls, text: str | None) -> SlaInput:
        usage = "Введите длительность: 45 (минут), 1ч 30м, 2h, 90s, 1d. Максимум 30 суток."
        raw = (text or "").strip().lower().replace(",", " ")
        tokens = _DURATION_TOKEN.findall(raw)
        if not tokens or _DURATION_TOKEN.sub("", raw).strip():
            raise CommandArgumentError(usage)
        total = 0
        for number, unit in tokens:
            if unit and unit not in _DURATION_UNITS:
                raise CommandArgumentError(usage)
            total += int(number) * _DURATION_UNITS.get(unit, 60)
        try:
            return cls(sla_seconds=total)
        except ValidationError as exc:
            raise CommandArgumentError(usage) from exc


class ChangeTimezoneCommand(Schema):
    timezone: TimezoneName

    @classmethod
    def parse(cls, args: str | None) -> ChangeTimezoneCommand:
        parts = _split(args)
        try:
            if len(parts) != 1:
                raise ValueError
            return cls(timezone=parts[0])
        except (ValidationError, ValueError) as exc:
            raise CommandArgumentError(
                "Укажите часовой пояс IANA, например Europe/Moscow или Asia/Yekaterinburg"
            ) from exc


class SetNotificationCommand(Schema):
    notification_chat_id: TelegramChatId
    notification_thread_id: ThreadId = None

    @classmethod
    def parse(cls, args: str | None) -> SetNotificationCommand:
        usage = "Укажите chat_id и, при необходимости, ID топика: -1001234567890 42"
        parts = _split(args)
        if len(parts) not in (1, 2):
            raise CommandArgumentError(usage)
        try:
            chat_id = int(parts[0])
            thread_id = int(parts[1]) if len(parts) == 2 else None
            return cls(notification_chat_id=chat_id, notification_thread_id=thread_id)
        except (ValidationError, ValueError) as exc:
            raise CommandArgumentError(
                f"{usage}\nchat_id — ненулевое целое, thread_id — положительное целое"
            ) from exc


class StatsCommand(Schema):
    days: int = Field(default=30, ge=1, le=365)

    @classmethod
    def parse(cls, args: str | None) -> StatsCommand:
        parts = _split(args)
        try:
            if not parts:
                return cls()
            if len(parts) != 1:
                raise ValueError
            return cls(days=int(parts[0]))
        except (ValidationError, ValueError) as exc:
            raise CommandArgumentError(
                "Использование: /stats [days], days от 1 до 365 (по умолчанию 30)"
            ) from exc
