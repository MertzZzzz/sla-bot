from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

BOT_SETTINGS_ID = 1


class BotSettings(TimestampMixin, Base):
    """Global settings edited in the bot menu (a single row, ``id = 1``)."""

    __tablename__ = "bot_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=BOT_SETTINGS_ID)
    # Forum group for notifications of newly added chats: each new chat gets its own
    # topic there, named like the chat.
    default_notification_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    default_notification_title: Mapped[str | None] = mapped_column(String(256))
    updated_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
