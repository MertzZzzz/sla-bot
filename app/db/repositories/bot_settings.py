from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotSettings
from app.db.models.bot_settings import BOT_SETTINGS_ID


class BotSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> BotSettings | None:
        return await self._session.get(BotSettings, BOT_SETTINGS_ID)

    async def set_default_notification(
        self, chat_id: int | None, title: str | None, actor_id: int
    ) -> None:
        values = {
            "default_notification_chat_id": chat_id,
            "default_notification_title": title,
            "updated_by_telegram_id": actor_id,
        }
        stmt = insert(BotSettings).values(id=BOT_SETTINGS_ID, **values)
        await self._session.execute(
            stmt.on_conflict_do_update(index_elements=[BotSettings.id], set_=values)
        )
