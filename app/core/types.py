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


TimezoneName = Annotated[str, AfterValidator(validate_timezone)]
# Telegram user IDs are always positive.
TelegramUserId = Annotated[int, Field(gt=0)]
# Group/supergroup chat IDs are negative, private chats positive; zero is never valid.
TelegramChatId = Annotated[int, Field(ne=0)]
TelegramMessageId = Annotated[int, Field(gt=0)]
# message_thread_id is either absent or a positive ID.
ThreadId = Annotated[int | None, Field(default=None, gt=0)]
SlaSeconds = Annotated[int, Field(gt=0, le=60 * 60 * 24 * 30)]
