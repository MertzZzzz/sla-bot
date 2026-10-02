"""Global administrators: .env ones (immutable from the bot) plus menu-added ones."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.db.models import BotAdmin
from app.db.models.admins import ADMIN_SOURCE_ENV
from app.db.uow import UnitOfWork
from app.schemas.users import TelegramUserData, TelegramUserRead
from app.services.clock import Clock


@dataclass(frozen=True, slots=True)
class AdminView:
    telegram_user_id: int
    user: TelegramUserRead | None
    from_env: bool
    notify_new_chats: bool
    added_by: TelegramUserRead | None
    added_by_telegram_id: int | None
    created_at: datetime

    @property
    def label(self) -> str:
        if self.user and self.user.username:
            return f"@{self.user.username}"
        if self.user and self.user.display_name:
            return self.user.display_name
        return str(self.telegram_user_id)


class AdminChange(StrEnum):
    OK = "ok"
    ALREADY = "already"
    NOT_FOUND = "not_found"
    PROTECTED = "protected"  # .env admin cannot be removed from the bot
    SELF = "self"  # an admin cannot remove themselves (avoid locking yourself out)


class AdminService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], clock: Clock, env_admin_ids: frozenset[int]
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._env_ids = env_admin_ids

    async def sync_env_admins(self) -> None:
        async with self._uow_factory() as uow:
            await uow.admins.sync_env(self._env_ids)
            await uow.commit()

    async def is_admin(self, telegram_user_id: int | None) -> bool:
        if telegram_user_id is None:
            return False
        if telegram_user_id in self._env_ids:
            return True
        async with self._uow_factory() as uow:
            return await uow.admins.exists(telegram_user_id)

    async def list_admins(self) -> list[AdminView]:
        async with self._uow_factory() as uow:
            rows = await uow.admins.list_all()
            return [await self._view(uow, row) for row in rows]

    async def get(self, telegram_user_id: int) -> AdminView | None:
        async with self._uow_factory() as uow:
            row = await uow.admins.get(telegram_user_id)
            return await self._view(uow, row) if row else None

    async def add(self, user: TelegramUserData, actor_id: int) -> AdminChange:
        async with self._uow_factory() as uow:
            db_user = (
                await uow.users.upsert(user, self._clock.now())
                if _has_name(user)
                else (await uow.users.get_by_telegram_id(user.telegram_user_id))
            )
            row = await uow.admins.add(
                user.telegram_user_id, db_user.id if db_user else None, actor_id
            )
            if row is None:
                return AdminChange.ALREADY
            await uow.commit()
        return AdminChange.OK

    async def remove(self, telegram_user_id: int, actor_id: int) -> AdminChange:
        if telegram_user_id == actor_id:
            return AdminChange.SELF
        async with self._uow_factory() as uow:
            row = await uow.admins.get(telegram_user_id, for_update=True)
            if row is None:
                return AdminChange.NOT_FOUND
            if row.source == ADMIN_SOURCE_ENV or telegram_user_id in self._env_ids:
                return AdminChange.PROTECTED
            await uow.admins.remove(telegram_user_id)
            await uow.commit()
        return AdminChange.OK

    async def toggle_notify_new_chats(self, telegram_user_id: int) -> bool | None:
        async with self._uow_factory() as uow:
            row = await uow.admins.get(telegram_user_id, for_update=True)
            if row is None:
                return None
            row.notify_new_chats = not row.notify_new_chats
            value = row.notify_new_chats
            await uow.commit()
        return value

    async def new_chat_recipients(self) -> list[int]:
        async with self._uow_factory() as uow:
            return [r.telegram_user_id for r in await uow.admins.list_all() if r.notify_new_chats]

    @staticmethod
    async def _view(uow: UnitOfWork, row: BotAdmin) -> AdminView:
        user = await uow.users.get_by_telegram_id(row.telegram_user_id)
        added_by = (
            await uow.users.get_by_telegram_id(row.added_by_telegram_id)
            if row.added_by_telegram_id
            else None
        )
        return AdminView(
            telegram_user_id=row.telegram_user_id,
            user=TelegramUserRead.model_validate(user) if user else None,
            from_env=row.source == ADMIN_SOURCE_ENV,
            notify_new_chats=row.notify_new_chats,
            added_by=TelegramUserRead.model_validate(added_by) if added_by else None,
            added_by_telegram_id=row.added_by_telegram_id,
            created_at=row.created_at,
        )


def _has_name(user: TelegramUserData) -> bool:
    return bool(user.first_name or user.last_name or user.username)
