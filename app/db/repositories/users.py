from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import TelegramUser
from app.schemas.users import TelegramUserData


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, data: TelegramUserData, now: datetime) -> TelegramUser:
        values = {
            "telegram_user_id": data.telegram_user_id,
            "username": data.username,
            "first_name": data.first_name,
            "last_name": data.last_name,
            "display_name": data.display_name,
            "is_bot": data.is_bot,
            "last_seen_at": now,
        }
        stmt = (
            insert(TelegramUser)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[TelegramUser.telegram_user_id],
                set_={
                    **{k: v for k, v in values.items() if k != "telegram_user_id"},
                    "updated_at": now,
                },
            )
            .returning(TelegramUser)
        )
        result = await self._session.scalars(stmt, execution_options={"populate_existing": True})
        return result.one()

    async def get_by_telegram_id(self, telegram_user_id: int) -> TelegramUser | None:
        user: TelegramUser | None = await self._session.scalar(
            select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user_id)
        )
        return user

    async def get_by_username(self, username: str) -> TelegramUser | None:
        user: TelegramUser | None = await self._session.scalar(
            select(TelegramUser)
            .where(func.lower(TelegramUser.username) == username.lower().lstrip("@"))
            .order_by(TelegramUser.last_seen_at.desc())
            .limit(1)
        )
        return user

    async def get(self, user_id: int) -> TelegramUser | None:
        return await self._session.get(TelegramUser, user_id)

    async def map_by_telegram_ids(self, telegram_ids: Iterable[int]) -> dict[int, TelegramUser]:
        ids = set(telegram_ids)
        if not ids:
            return {}
        rows = await self._session.scalars(
            select(TelegramUser).where(TelegramUser.telegram_user_id.in_(ids))
        )
        return {u.telegram_user_id: u for u in rows}


class SyncUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_id(self, telegram_user_id: int) -> TelegramUser | None:
        return self._session.scalar(
            select(TelegramUser).where(TelegramUser.telegram_user_id == telegram_user_id)
        )
