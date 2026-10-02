from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import BotAdmin
from app.db.models.admins import ADMIN_SOURCE_BOT, ADMIN_SOURCE_ENV


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, telegram_user_id: int, *, for_update: bool = False) -> BotAdmin | None:
        return await self._session.get(BotAdmin, telegram_user_id, with_for_update=for_update)

    async def exists(self, telegram_user_id: int) -> bool:
        stmt = select(BotAdmin.telegram_user_id).where(
            BotAdmin.telegram_user_id == telegram_user_id
        )
        return (await self._session.scalar(stmt)) is not None

    async def list_all(self) -> list[BotAdmin]:
        stmt = select(BotAdmin).order_by(BotAdmin.source.desc(), BotAdmin.created_at)
        return list(await self._session.scalars(stmt))

    async def add(
        self, telegram_user_id: int, user_id: int | None, added_by: int | None
    ) -> BotAdmin | None:
        """Insert a menu-added admin; ``None`` if this user is already an admin."""
        stmt = (
            insert(BotAdmin)
            .values(
                telegram_user_id=telegram_user_id,
                user_id=user_id,
                source=ADMIN_SOURCE_BOT,
                added_by_telegram_id=added_by,
            )
            .on_conflict_do_nothing()
            .returning(BotAdmin)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def remove(self, telegram_user_id: int) -> None:
        await self._session.execute(
            delete(BotAdmin).where(BotAdmin.telegram_user_id == telegram_user_id)
        )

    async def sync_env(self, env_ids: Iterable[int]) -> None:
        """Mirror .env admins: upsert current ones, drop env rows no longer configured."""
        ids = set(env_ids)
        if ids:
            stmt = insert(BotAdmin).values(
                [{"telegram_user_id": i, "source": ADMIN_SOURCE_ENV} for i in ids]
            )
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[BotAdmin.telegram_user_id],
                    set_={"source": ADMIN_SOURCE_ENV},
                )
            )
        stale = delete(BotAdmin).where(BotAdmin.source == ADMIN_SOURCE_ENV)
        if ids:
            stale = stale.where(BotAdmin.telegram_user_id.not_in(ids))
        await self._session.execute(stale)


class SyncAdminRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def telegram_ids(self) -> list[int]:
        return list(self._session.scalars(select(BotAdmin.telegram_user_id)))
