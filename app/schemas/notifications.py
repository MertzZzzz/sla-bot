from __future__ import annotations

from datetime import datetime

from app.core.enums import Priority
from app.core.types import TelegramChatId, ThreadId
from app.schemas.common import Schema


class NotificationPayload(Schema):
    """Everything required to render and deliver an SLA notification.

    Stored in the outbox event so that delivery does not depend on later
    configuration changes.
    """

    pending_reply_id: int
    target_chat_id: TelegramChatId | None
    target_thread_id: ThreadId = None
    chat_title: str
    priority: Priority
    sla_seconds: int
    responsible_telegram_id: int | None
    responsible_name: str | None
    author_name: str
    author_telegram_id: int
    source_text: str | None
    source_content_type: str | None
    source_message_date: datetime
    deadline_at: datetime
    message_link: str | None
    timezone: str
    chat_link: str | None = None
    # Follow-up messages merged into the ticket (defaults keep old payloads valid).
    message_count: int = 1
    last_message_text: str | None = None
    last_content_type: str | None = None
    last_message_date: datetime | None = None


class RenderedNotification(Schema):
    text: str
    target_chat_id: int
    target_thread_id: int | None
