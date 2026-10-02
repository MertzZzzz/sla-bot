"""Validated arguments of admin commands."""

from __future__ import annotations

from pydantic import Field, ValidationError, field_validator

from app.core.enums import Priority, ReplyMatchMode
from app.core.types import SlaSeconds, TelegramChatId, ThreadId, TimezoneName
from app.schemas.common import Schema


class CommandArgumentError(ValueError):
    """Raised when command arguments are invalid; message is user-facing."""


def _split(args: str | None) -> list[str]:
    return (args or "").split()


class ChangeSlaCommand(Schema):
    sla_seconds: SlaSeconds

    @classmethod
    def parse(cls, args: str | None) -> ChangeSlaCommand:
        parts = _split(args)
        if len(parts) != 1 or not parts[0].isdigit():
            raise CommandArgumentError("Использование: /chat_sla <секунды>, например /chat_sla 900")
        try:
            return cls(sla_seconds=int(parts[0]))
        except ValidationError as exc:
            raise CommandArgumentError(
                "SLA должен быть положительным числом секунд (не более 30 суток)"
            ) from exc


class ChangePriorityCommand(Schema):
    priority: Priority

    @field_validator("priority", mode="before")
    @classmethod
    def lower(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @classmethod
    def parse(cls, args: str | None) -> ChangePriorityCommand:
        parts = _split(args)
        try:
            if len(parts) != 1:
                raise ValueError
            return cls(priority=parts[0])  # type: ignore[arg-type]
        except (ValidationError, ValueError) as exc:
            raise CommandArgumentError("Использование: /chat_priority <p1|p2|p3|p4>") from exc


class ChangeModeCommand(Schema):
    mode: ReplyMatchMode

    @classmethod
    def parse(cls, args: str | None) -> ChangeModeCommand:
        parts = _split(args)
        try:
            if len(parts) != 1:
                raise ValueError
            return cls(mode=parts[0].lower())  # type: ignore[arg-type]
        except (ValidationError, ValueError) as exc:
            modes = "|".join(m.value for m in ReplyMatchMode)
            raise CommandArgumentError(f"Использование: /chat_mode <{modes}>") from exc


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
                "Использование: /chat_timezone <IANA timezone>, например Europe/Moscow"
            ) from exc


class SetNotificationCommand(Schema):
    notification_chat_id: TelegramChatId
    notification_thread_id: ThreadId = None

    @classmethod
    def parse(cls, args: str | None) -> SetNotificationCommand:
        usage = "Использование: /chat_notification <notification_chat_id> [message_thread_id]"
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
