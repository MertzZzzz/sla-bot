"""Global bot settings edited in the menu (stored in PostgreSQL, not in .env)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.db.uow import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DefaultNotificationTarget:
    """Forum group where every newly added chat gets its own notification topic."""

    chat_id: int
    title: str


class BotSettingsService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def default_notification(self) -> DefaultNotificationTarget | None:
        async with self._uow_factory() as uow:
            row = await uow.bot_settings.get()
            if row is None or row.default_notification_chat_id is None:
                return None
            return DefaultNotificationTarget(
                chat_id=row.default_notification_chat_id,
                title=row.default_notification_title or str(row.default_notification_chat_id),
            )

    async def set_default_notification(
        self, target: DefaultNotificationTarget | None, actor_id: int
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.bot_settings.set_default_notification(
                target.chat_id if target else None,
                target.title[:256] if target else None,
                actor_id,
            )
            await uow.commit()
        logger.info(
            "default notification chat changed",
            extra={
                "event": "default_notification_changed",
                "telegram_chat_id": target.chat_id if target else None,
                "telegram_user_id": actor_id,
            },
        )
