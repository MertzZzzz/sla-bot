from __future__ import annotations

from enum import StrEnum


class Priority(StrEnum):
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"

    @property
    def label(self) -> str:
        return self.value.upper()


class ReplyMatchMode(StrEnum):
    ANY_RESPONDER_MESSAGE = "any_responder_message"
    REPLY_ONLY = "reply_only"
    THREAD_OR_REPLY = "thread_or_reply"


class ChatType(StrEnum):
    GROUP = "group"
    SUPERGROUP = "supergroup"


class PendingReplyStatus(StrEnum):
    WAITING = "waiting"
    OVERDUE = "overdue"
    ANSWERED = "answered"
    NOT_REQUIRED = "not_required"
    CANCELLED = "cancelled"

    @classmethod
    def open_statuses(cls) -> tuple[PendingReplyStatus, ...]:
        return (cls.WAITING, cls.OVERDUE)

    @classmethod
    def final_statuses(cls) -> tuple[PendingReplyStatus, ...]:
        return (cls.ANSWERED, cls.NOT_REQUIRED, cls.CANCELLED)


class ReplyEventType(StrEnum):
    CREATED = "created"
    ANSWERED = "answered"
    OVERDUE = "overdue"
    NOT_REQUIRED = "not_required"
    CANCELLED = "cancelled"
    REASSIGNED = "reassigned"
    MESSAGE_ADDED = "message_added"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutboxEventType(StrEnum):
    SLA_OVERDUE_NOTIFICATION = "sla_overdue_notification"


class AggregateType(StrEnum):
    PENDING_REPLY = "pending_reply"


class ContentType(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    STICKER = "sticker"
    CONTACT = "contact"
    LOCATION = "location"
    VENUE = "venue"
    POLL = "poll"
    OTHER = "other"


class ChatConfigAction(StrEnum):
    CHAT_ADDED = "chat_added"
    CHAT_ENABLED = "chat_enabled"
    CHAT_DISABLED = "chat_disabled"
    CHAT_MIGRATED = "chat_migrated"
    SLA_CHANGED = "sla_changed"
    PRIORITY_CHANGED = "priority_changed"
    MODE_CHANGED = "mode_changed"
    TIMEZONE_CHANGED = "timezone_changed"
    RESPONSIBLE_CHANGED = "responsible_changed"
    RESPONDER_ADDED = "responder_added"
    RESPONDER_REMOVED = "responder_removed"
    NOTIFICATION_CHANGED = "notification_changed"
    PILOT_INVITED = "pilot_invited"
    PILOT_DATES_CHANGED = "pilot_dates_changed"
    CHAT_DELETED = "chat_deleted"
