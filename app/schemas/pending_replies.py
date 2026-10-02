from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.core.enums import ContentType, PendingReplyStatus, Priority
from app.core.types import TelegramChatId, TelegramMessageId, TelegramUserId, ThreadId
from app.schemas.common import Schema
from app.schemas.users import TelegramUserData


class IncomingMessage(Schema):
    """Transport-agnostic view of a Telegram group message relevant for SLA tracking."""

    update_id: int | None = None
    telegram_chat_id: TelegramChatId
    chat_title: str
    chat_type: str
    chat_username: str | None = None
    message_id: TelegramMessageId
    message_thread_id: ThreadId = None
    is_topic_message: bool = False
    reply_to_message_id: int | None = None
    date: datetime
    author: TelegramUserData
    text: str | None = None
    content_type: ContentType = ContentType.TEXT


class PendingReplyCreate(Schema):
    chat_id: int
    source_message_id: TelegramMessageId
    source_thread_id: ThreadId = None
    source_author_user_id: int | None
    source_author_telegram_id: TelegramUserId
    source_author_name: str | None
    source_text: str | None
    source_content_type: ContentType
    source_message_date: datetime
    source_message_link: str | None
    created_at: datetime
    deadline_at: datetime
    priority_snapshot: Priority
    sla_seconds_snapshot: int = Field(gt=0)
    responsible_telegram_id_snapshot: int | None


class PendingReplyRead(Schema):
    id: int
    chat_id: int
    source_message_id: int
    source_thread_id: int | None
    source_author_telegram_id: int
    source_author_name: str | None
    source_text: str | None
    source_content_type: str | None
    source_message_date: datetime
    source_message_link: str | None
    created_at: datetime
    deadline_at: datetime
    priority_snapshot: Priority
    sla_seconds_snapshot: int
    responsible_telegram_id_snapshot: int | None
    message_count: int = 1
    last_message_at: datetime | None = None
    last_message_text: str | None = None
    status: PendingReplyStatus
    overdue_at: datetime | None
    responded_at: datetime | None
    responded_by_telegram_id: int | None
    response_message_id: int | None
    notification_chat_id: int | None
    notification_message_id: int | None
    notification_sent_at: datetime | None
    not_required_at: datetime | None
    not_required_by_telegram_id: int | None


class PendingReplyCloseRequest(Schema):
    chat_id: int
    responder_user_id: int
    responder_telegram_id: TelegramUserId
    response_message_id: TelegramMessageId
    response_thread_id: ThreadId = None
    reply_to_message_id: int | None = None
    responded_at: datetime


class MessageProcessingResult(Schema):
    action: str  # ignored | created | merged | duplicate | answered | no_match
    pending_reply_id: int | None = None
    reason: str | None = None
