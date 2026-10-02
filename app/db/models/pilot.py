from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PilotParticipant(TimestampMixin, Base):
    """Person to invite into customer chats with /invite_all.

    Usually added by @username; the Bot API cannot resolve a username to a user ID, so
    ``telegram_user_id`` is filled in once the bot sees the person (e.g. /start).
    """

    __tablename__ = "pilot_participants"
    __table_args__ = (
        CheckConstraint("username IS NOT NULL OR telegram_user_id IS NOT NULL", name="identified"),
        Index("uq_pilot_participants_username", text("lower(username)"), unique=True),
        Index(
            "uq_pilot_participants_telegram_user_id",
            "telegram_user_id",
            unique=True,
            postgresql_where=text("telegram_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(64))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    display_name: Mapped[str | None] = mapped_column(String(512))
    added_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
