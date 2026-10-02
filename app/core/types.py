"""Reusable annotated types with validation."""

from __future__ import annotations

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, Field


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"Unknown IANA timezone: {value!r}"
        raise ValueError(msg) from exc
    return value


def validate_non_zero(value: int) -> int:
    if value == 0:
        msg = "Telegram chat ID must not be zero"
        raise ValueError(msg)
    return value


TimezoneName = Annotated[str, AfterValidator(validate_timezone)]
# Telegram user IDs are always positive.
TelegramUserId = Annotated[int, Field(gt=0)]
# Group/supergroup chat IDs are negative, private chats positive; zero is never valid.
TelegramChatId = Annotated[int, AfterValidator(validate_non_zero)]
TelegramMessageId = Annotated[int, Field(gt=0)]
# message_thread_id is either absent or a positive ID.
ThreadId = Annotated[Annotated[int, Field(gt=0)] | None, Field(default=None)]
SlaSeconds = Annotated[int, Field(gt=0, le=60 * 60 * 24 * 30)]
