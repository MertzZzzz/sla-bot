from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.core.types import (
    SlaSeconds,
    TelegramChatId,
    TelegramUserId,
    ThreadId,
    TimezoneName,
)
from app.schemas.common import Schema
from app.schemas.users import TelegramUserRead


class MonitoredChatCreate(Schema):
    telegram_chat_id: TelegramChatId
    thread_id: ThreadId = None
    topic_name: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    chat_type: ChatType
    priority: Priority
    sla_seconds: SlaSeconds
    reply_match_mode: ReplyMatchMode
    timezone: TimezoneName = "Europe/Moscow"
    notification_chat_id: TelegramChatId | None = None
    notification_thread_id: ThreadId = None


class MonitoredChatUpdate(Schema):
    """Partial update; only explicitly set fields are applied."""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    is_enabled: bool | None = None
    priority: Priority | None = None
    sla_seconds: SlaSeconds | None = None
    reply_match_mode: ReplyMatchMode | None = None
    timezone: TimezoneName | None = None
    notification_chat_id: TelegramChatId | None = None
    notification_thread_id: ThreadId = None


class MonitoredChatRead(Schema):
    id: int
    telegram_chat_id: int
    thread_id: int | None = None
    topic_name: str | None = None
    title: str
    chat_username: str | None = None
    chat_link: str | None = None
    chat_type: ChatType
    is_enabled: bool
    priority: Priority
    sla_seconds: int
    reply_match_mode: ReplyMatchMode
    responsible_user_id: int | None
    notification_chat_id: int | None
    notification_thread_id: int | None
    timezone: str
    created_at: datetime
    updated_at: datetime


class MonitoredChatDetails(Schema):
    chat: MonitoredChatRead
    responsible: TelegramUserRead | None
    responders: tuple[TelegramUserRead, ...]
    # Known participants (most recent first) and currently open tickets; filled for the
    # settings menu, empty/zero elsewhere.
    members: tuple[TelegramUserRead, ...] = ()
    open_tickets: int = 0
    # Error of the latest escalation if it could not be delivered.
    notification_error: str | None = None

    def candidates(self) -> list[TelegramUserRead]:
        """People to offer in pickers: participants plus current responders/responsible."""
        seen: dict[int, TelegramUserRead] = {}
        extra = (self.responsible,) if self.responsible else ()
        for user in (*self.members, *self.responders, *extra):
            seen.setdefault(user.telegram_user_id, user)
        return list(seen.values())


class ResponderAddRequest(Schema):
    telegram_chat_id: TelegramChatId
    user_telegram_id: TelegramUserId
    actor_telegram_id: TelegramUserId
