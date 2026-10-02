from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import PendingReplyStatus, Priority, ReplyEventType
from app.db.base import Base, TimestampMixin, pg_enum


class PendingReply(TimestampMixin, Base):
    """A message from an external participant that waits for a responder's answer."""

    __tablename__ = "pending_replies"
    __table_args__ = (
        UniqueConstraint("chat_id", "source_message_id", name="uq_pending_reply_source_message"),
        Index(
            "ix_pending_reply_due_waiting",
            "deadline_at",
            postgresql_where=text("status = 'waiting'"),
        ),
        Index(
            "ix_pending_reply_chat_status_created",
            "chat_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_pending_reply_responder_stats",
            "responded_by_telegram_id",
            "responded_at",
            postgresql_where=text("status = 'answered'"),
        ),
        Index("ix_pending_reply_created_at", "created_at"),
        # One responder message closes at most one ticket, even on redelivered updates.
        Index(
            "uq_pending_reply_response_message",
            "chat_id",
            "response_message_id",
            unique=True,
            postgresql_where=text("response_message_id IS NOT NULL"),
        ),
        CheckConstraint("sla_seconds_snapshot > 0", name="sla_snapshot_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_chats.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    source_author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL")
    )
    source_author_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_author_name: Mapped[str | None] = mapped_column(String(512))
    source_text: Mapped[str | None] = mapped_column(Text)
    source_content_type: Mapped[str | None] = mapped_column(String(32))
    source_message_date: Mapped[datetime] = mapped_column(nullable=False)
    source_message_link: Mapped[str | None] = mapped_column(String(256))
    # Consecutive messages of the same author are merged into one ticket (one answer
    # closes them all); ``source_*`` describe the first one, ``last_*`` the latest.
    message_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_message_at: Mapped[datetime | None] = mapped_column()
    last_message_text: Mapped[str | None] = mapped_column(Text)
    last_content_type: Mapped[str | None] = mapped_column(String(32))
    deadline_at: Mapped[datetime] = mapped_column(nullable=False)
    # Snapshots of the chat settings at ticket creation; later setting changes do not
    # rewrite existing obligations or historical statistics.
    priority_snapshot: Mapped[Priority] = mapped_column(
        pg_enum(Priority, "chat_priority"), nullable=False
    )
    sla_seconds_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    responsible_telegram_id_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[PendingReplyStatus] = mapped_column(
        pg_enum(PendingReplyStatus, "pending_reply_status"),
        default=PendingReplyStatus.WAITING,
        server_default=PendingReplyStatus.WAITING.value,
        nullable=False,
    )
    overdue_at: Mapped[datetime | None] = mapped_column()
    responded_at: Mapped[datetime | None] = mapped_column()
    responded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL")
    )
    responded_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    response_message_id: Mapped[int | None] = mapped_column(BigInteger)
    notification_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    notification_message_id: Mapped[int | None] = mapped_column(BigInteger)
    notification_sent_at: Mapped[datetime | None] = mapped_column()
    not_required_at: Mapped[datetime | None] = mapped_column()
    not_required_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL")
    )
    not_required_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)


class PendingReplyMessage(Base):
    """Every Telegram message that belongs to a ticket (the first one and merged ones)."""

    __tablename__ = "pending_reply_messages"
    __table_args__ = (Index("ix_pending_reply_messages_pending_reply_id", "pending_reply_id"),)

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_chats.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pending_reply_id: Mapped[int] = mapped_column(
        ForeignKey("pending_replies.id", ondelete="CASCADE"), nullable=False
    )
    message_date: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ReplyEvent(Base):
    """Audit log of domain transitions of a pending reply."""

    __tablename__ = "reply_events"
    __table_args__ = (Index("ix_reply_events_pending_reply_id", "pending_reply_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pending_reply_id: Mapped[int] = mapped_column(
        ForeignKey("pending_replies.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[ReplyEventType] = mapped_column(
        pg_enum(ReplyEventType, "reply_event_type"), nullable=False
    )
    actor_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
