from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ADMIN_SOURCE_ENV = "env"
ADMIN_SOURCE_BOT = "bot"


class BotAdmin(TimestampMixin, Base):
    """Global administrator.

    ``source='env'`` rows mirror APP_TELEGRAM__ADMIN_TELEGRAM_IDS (synced on bot start,
    cannot be removed from the menu); ``source='bot'`` rows are added via the menu.
    """

    __tablename__ = "bot_admins"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    notify_new_chats: Mapped[bool] = mapped_column(
        default=True, server_default="true", nullable=False
    )
    added_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
