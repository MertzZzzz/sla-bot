from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.db.base import Base, TimestampMixin, pg_enum


class MonitoredChat(TimestampMixin, Base):
    __tablename__ = "monitored_chats"
    __table_args__ = (CheckConstraint("sla_seconds > 0", name="sla_seconds_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
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
