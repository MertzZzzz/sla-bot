from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import ChatResponder, MonitoredChat, TelegramUser
from app.schemas.chats import MonitoredChatCreate


class MonitoredChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(
        self, telegram_chat_id: int, *, for_update: bool = False
    ) -> MonitoredChat | None:
        stmt = select(MonitoredChat).where(MonitoredChat.telegram_chat_id == telegram_chat_id)
        if for_update:
            stmt = stmt.with_for_update()
        chat: MonitoredChat | None = await self._session.scalar(stmt)
        return chat

    async def get(self, chat_id: int) -> MonitoredChat | None:
        return await self._session.get(MonitoredChat, chat_id)

    async def create_if_absent(self, data: MonitoredChatCreate) -> MonitoredChat | None:
        """Insert a chat; returns ``None`` when it is already monitored."""
        stmt = (
            insert(MonitoredChat)
            .values(**data.model_dump())
            .on_conflict_do_nothing(index_elements=[MonitoredChat.telegram_chat_id])
            .returning(MonitoredChat)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def update_fields(self, chat_id: int, values: Mapping[str, Any]) -> MonitoredChat:
        stmt = (
            update(MonitoredChat)
            .where(MonitoredChat.id == chat_id)
            .values(**values)
            .returning(MonitoredChat)
        )
        result = await self._session.scalars(stmt, execution_options={"populate_existing": True})
        return result.one()


class ResponderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_responder(self, chat_id: int, user_id: int) -> bool:
        stmt = select(ChatResponder.user_id).where(
            ChatResponder.chat_id == chat_id, ChatResponder.user_id == user_id
        )
        return (await self._session.scalar(stmt)) is not None

    async def add(self, chat_id: int, user_id: int, created_by: int | None) -> bool:
        stmt = (
            insert(ChatResponder)
            .values(chat_id=chat_id, user_id=user_id, created_by_telegram_user_id=created_by)
            .on_conflict_do_nothing()
            .returning(ChatResponder.user_id)
        )
        return (await self._session.scalar(stmt)) is not None

    async def remove(self, chat_id: int, user_id: int) -> bool:
        row = await self._session.get(ChatResponder, (chat_id, user_id))
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_users(self, chat_id: int) -> list[TelegramUser]:
        stmt = (
            select(TelegramUser)
            .join(ChatResponder, ChatResponder.user_id == TelegramUser.id)
            .where(ChatResponder.chat_id == chat_id)
            .order_by(ChatResponder.created_at)
        )
        return list(await self._session.scalars(stmt))


class SyncMonitoredChatRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, chat_id: int) -> MonitoredChat | None:
        return self._session.get(MonitoredChat, chat_id)
