from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.db.base import Base, TimestampMixin, pg_enum


class MonitoredChat(TimestampMixin, Base):
    __tablename__ = "monitored_chats"
    __table_args__ = (
        CheckConstraint("sla_seconds > 0", name="sla_seconds_positive"),
        # A whole group (thread_id NULL) or one forum topic of it is monitored; several
        # topics of one forum can be separate customer chats.
        Index(
            "uq_monitored_chats_chat_thread",
            "telegram_chat_id",
            func.coalesce(text("thread_id"), 0),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Forum topic (message_thread_id) when only one topic of the group is monitored.
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    topic_name: Mapped[str | None] = mapped_column(String(128))
    # Display title: "<group>" or "<group> / <topic>".
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    # Public @username of the chat (if any) and a link that opens it, used in
    # notifications: t.me/<username> or an invite link created by the bot.
    chat_username: Mapped[str | None] = mapped_column(String(64))
    chat_link: Mapped[str | None] = mapped_column(String(256))
    chat_type: Mapped[ChatType] = mapped_column(pg_enum(ChatType, "chat_type"), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    priority: Mapped[Priority] = mapped_column(pg_enum(Priority, "chat_priority"), nullable=False)
    sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_match_mode: Mapped[ReplyMatchMode] = mapped_column(
        pg_enum(ReplyMatchMode, "reply_match_mode"), nullable=False
    )
    responsible_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL")
    )
    notification_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    notification_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    timezone: Mapped[str] = mapped_column(
        String(64), default="Europe/Moscow", server_default="Europe/Moscow", nullable=False
    )


class ChatResponder(Base):
    __tablename__ = "chat_responders"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)


class ChatMember(Base):
    """Users seen writing in a monitored chat; offered as candidates in the settings menu."""

    __tablename__ = "chat_members"
    __table_args__ = (Index("ix_chat_members_chat_last_seen", "chat_id", "last_seen_at"),)

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("monitored_chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE"), primary_key=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ForumTopic(Base):
    """Topic names seen in forum groups; the Bot API has no method to list topics."""

    __tablename__ = "forum_topics"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
