from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import ChatMember, ChatResponder, ForumTopic, MonitoredChat, TelegramUser
from app.schemas.chats import MonitoredChatCreate


class MonitoredChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(
        self, telegram_chat_id: int, *, thread_id: int | None = None, for_update: bool = False
    ) -> MonitoredChat | None:
        """The whole-group chat (``thread_id=None``) or exactly that topic."""
        thread = (
            MonitoredChat.thread_id.is_(None)
            if thread_id is None
            else MonitoredChat.thread_id == thread_id
        )
        stmt = select(MonitoredChat).where(
            MonitoredChat.telegram_chat_id == telegram_chat_id, thread
        )
        if for_update:
            stmt = stmt.with_for_update()
        chat: MonitoredChat | None = await self._session.scalar(stmt)
        return chat

    async def get_for_message(
        self, telegram_chat_id: int, thread_id: int | None
    ) -> MonitoredChat | None:
        """Chat a message belongs to: its topic if that topic is monitored, else the group."""
        if thread_id is not None:
            topic = await self.get_by_telegram_id(telegram_chat_id, thread_id=thread_id)
            if topic is not None:
                return topic
        return await self.get_by_telegram_id(telegram_chat_id)

    async def list_for_group(self, telegram_chat_id: int) -> list[MonitoredChat]:
        stmt = select(MonitoredChat).where(MonitoredChat.telegram_chat_id == telegram_chat_id)
        return list(await self._session.scalars(stmt))

    async def remember_topic(
        self, telegram_chat_id: int, thread_id: int, name: str, now: datetime
    ) -> None:
        stmt = (
            insert(ForumTopic)
            .values(
                telegram_chat_id=telegram_chat_id, thread_id=thread_id, name=name, updated_at=now
            )
            .on_conflict_do_update(
                index_elements=[ForumTopic.telegram_chat_id, ForumTopic.thread_id],
                set_={"name": name, "updated_at": now},
            )
        )
        await self._session.execute(stmt)

    async def topics(self, telegram_chat_id: int) -> list[ForumTopic]:
        stmt = (
            select(ForumTopic)
            .where(ForumTopic.telegram_chat_id == telegram_chat_id)
            .order_by(ForumTopic.name)
        )
        return list(await self._session.scalars(stmt))

    async def topic_name(self, telegram_chat_id: int, thread_id: int) -> str | None:
        topic = await self._session.get(ForumTopic, (telegram_chat_id, thread_id))
        return topic.name if topic else None

    async def list_all(self) -> list[MonitoredChat]:
        stmt = select(MonitoredChat).order_by(MonitoredChat.title, MonitoredChat.id)
        return list(await self._session.scalars(stmt))

    async def get(self, chat_id: int, *, for_update: bool = False) -> MonitoredChat | None:
        return await self._session.get(MonitoredChat, chat_id, with_for_update=for_update)

    async def create_if_absent(self, data: MonitoredChatCreate) -> MonitoredChat | None:
        """Insert a chat; returns ``None`` when it is already monitored."""
        stmt = (
            insert(MonitoredChat)
            .values(**data.model_dump())
            .on_conflict_do_nothing()  # same group/topic already monitored
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


class ChatMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def touch(self, chat_id: int, user_id: int, now: datetime) -> None:
        stmt = (
            insert(ChatMember)
            .values(chat_id=chat_id, user_id=user_id, first_seen_at=now, last_seen_at=now)
            .on_conflict_do_update(
                index_elements=[ChatMember.chat_id, ChatMember.user_id],
                set_={"last_seen_at": now},
            )
        )
        await self._session.execute(stmt)

    async def list_users(self, chat_id: int) -> list[TelegramUser]:
        """Known people of the chat (most recently active first), bots excluded."""
        stmt = (
            select(TelegramUser)
            .join(ChatMember, ChatMember.user_id == TelegramUser.id)
            .where(ChatMember.chat_id == chat_id, TelegramUser.is_bot.is_(False))
            .order_by(ChatMember.last_seen_at.desc(), TelegramUser.id)
        )
        return list(await self._session.scalars(stmt))


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

    async def get_responder(self, chat_id: int, telegram_user_id: int) -> TelegramUser | None:
        stmt = (
            select(TelegramUser)
            .join(ChatResponder, ChatResponder.user_id == TelegramUser.id)
            .where(
                ChatResponder.chat_id == chat_id,
                TelegramUser.telegram_user_id == telegram_user_id,
            )
        )
        user: TelegramUser | None = await self._session.scalar(stmt)
        return user

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
