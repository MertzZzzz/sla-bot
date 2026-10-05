from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import AppSettings
from app.core.enums import (
    ChatConfigAction,
    ChatType,
    OutboxStatus,
    PendingReplyStatus,
    ReplyEventType,
)
from app.db.models import MonitoredChat, TelegramUser
from app.db.uow import UnitOfWork
from app.schemas.chats import (
    MonitoredChatCreate,
    MonitoredChatDetails,
    MonitoredChatRead,
    MonitoredChatUpdate,
)
from app.schemas.users import TelegramUserData, TelegramUserRead
from app.services.audit import AuditService
from app.services.clock import Clock


class ChatNotMonitoredError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ResponderChange:
    changed: bool
    user: TelegramUserRead


UPDATE_ACTIONS: dict[str, ChatConfigAction] = {
    "is_enabled": ChatConfigAction.CHAT_ENABLED,
    "sla_seconds": ChatConfigAction.SLA_CHANGED,
    "priority": ChatConfigAction.PRIORITY_CHANGED,
    "reply_match_mode": ChatConfigAction.MODE_CHANGED,
    "timezone": ChatConfigAction.TIMEZONE_CHANGED,
    "notification_chat_id": ChatConfigAction.NOTIFICATION_CHANGED,
    "notification_thread_id": ChatConfigAction.NOTIFICATION_CHANGED,
}


NOTIFICATION_FIELDS = {"notification_chat_id", "notification_thread_id"}


def topic_title(group_title: str, thread_id: int | None, topic_name: str | None) -> str:
    return f"{group_title} / {topic_name or f'тема #{thread_id}'}"


def public_link(username: str, thread_id: int | None) -> str:
    return f"https://t.me/{username}/{thread_id}" if thread_id else f"https://t.me/{username}"


def _jsonable(chat: MonitoredChat, fields: set[str]) -> dict[str, Any]:
    return MonitoredChatRead.model_validate(chat).model_dump(mode="json", include=fields)


class ChatSettingsService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        audit: AuditService,
        defaults: AppSettings,
        env_admin_ids: frozenset[int] = frozenset(),
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._audit = audit
        self._defaults = defaults
        self._env_admin_ids = env_admin_ids
        # Per-process cache: avoid a DB write for every message in a topic.
        self._known_topics: dict[tuple[int, int], str] = {}

    async def add_chat(
        self,
        telegram_chat_id: int,
        title: str,
        chat_type: ChatType,
        actor_id: int,
        *,
        username: str | None = None,
        thread_id: int | None = None,
        topic_name: str | None = None,
    ) -> tuple[bool, MonitoredChatRead]:
        """Start monitoring a group (or one forum topic of it). Returns ``(created, chat)``."""
        group_title = title or str(telegram_chat_id)
        data = MonitoredChatCreate(
            telegram_chat_id=telegram_chat_id,
            thread_id=thread_id,
            topic_name=topic_name[:128] if topic_name else None,
            title=(topic_title(group_title, thread_id, topic_name) if thread_id else group_title)[
                :256
            ],
            chat_type=chat_type,
            priority=self._defaults.default_priority,
            sla_seconds=self._defaults.default_sla_seconds,
            reply_match_mode=self._defaults.default_reply_match_mode,
            timezone=self._defaults.default_timezone,
        )
        async with self._uow_factory() as uow:
            chat = await uow.chats.create_if_absent(data)
            created = chat is not None
            if chat is None:
                chat = await self._require(uow, telegram_chat_id, thread_id)
            else:
                await self._audit.record_chat_change(
                    uow,
                    chat_id=chat.id,
                    actor_telegram_user_id=actor_id,
                    action=ChatConfigAction.CHAT_ADDED,
                    new_value=data.model_dump(mode="json"),
                )
                if self._defaults.admins_as_default_responders:
                    await self._add_admins_as_responders(uow, chat, actor_id)
            if username and chat.chat_username != username:
                chat.chat_username = username
                chat.chat_link = public_link(username, thread_id)
            await uow.session.flush()
            result = MonitoredChatRead.model_validate(chat)
            await uow.commit()
        return created, result

    async def _add_admins_as_responders(
        self, uow: UnitOfWork, chat: MonitoredChat, actor_id: int
    ) -> None:
        """New chats start with every global admin as a responder (removable in the menu)."""
        admin_ids = self._env_admin_ids | {a.telegram_user_id for a in await uow.admins.list_all()}
        added = []
        for telegram_id in sorted(admin_ids):
            user = await uow.users.ensure(telegram_id)
            if await uow.responders.add(chat.id, user.id, actor_id):
                added.append(telegram_id)
        if added:
            await self._audit.record_chat_change(
                uow,
                chat_id=chat.id,
                actor_telegram_user_id=actor_id,
                action=ChatConfigAction.RESPONDER_ADDED,
                new_value={"telegram_user_ids": added, "source": "admins_by_default"},
            )

    async def set_chat_link(self, chat_id: int, link: str) -> None:
        async with self._uow_factory() as uow:
            chat = await uow.chats.get(chat_id, for_update=True)
            if chat is not None and chat.chat_link != link:
                chat.chat_link = link
                await uow.commit()

    async def update(
        self,
        telegram_chat_id: int,
        changes: MonitoredChatUpdate,
        actor_id: int,
        *,
        thread_id: int | None = None,
    ) -> MonitoredChatRead:
        values = changes.model_dump(exclude_unset=True)
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, thread_id, for_update=True)
            fields = set(values)
            old = _jsonable(chat, fields)
            updated = await uow.chats.update_fields(chat.id, values)
            new = _jsonable(updated, fields)
            if values.get("is_enabled") is False:
                await self._cancel_open_tickets(uow, chat.id, actor_id)
            if old != new and values.keys() & NOTIFICATION_FIELDS:
                # Escalations that failed (e.g. wrong chat ID) are retried at the new target.
                await uow.outbox.requeue_failed(chat.id, self._clock.now())
            if old != new:
                await self._audit.record_chat_change(
                    uow,
                    chat_id=chat.id,
                    actor_telegram_user_id=actor_id,
                    action=self._action_for(values),
                    old_value=old,
                    new_value=new,
                )
            result = MonitoredChatRead.model_validate(updated)
            await uow.commit()
        return result

    @staticmethod
    async def _cancel_open_tickets(uow: UnitOfWork, chat_id: int, actor_id: int) -> None:
        """Monitoring is off: open tickets will never be escalated, so close them as
        ``cancelled`` (excluded from SLA metrics) instead of leaving them open forever."""
        for ticket in await uow.pending.lock_open(chat_id):
            previous = ticket.status
            ticket.status = PendingReplyStatus.CANCELLED
            await uow.audit.add_reply_event(
                ticket.id,
                ReplyEventType.CANCELLED,
                actor_telegram_user_id=actor_id,
                metadata={"reason": "chat_disabled", "previous_status": previous.value},
            )

    @staticmethod
    def _action_for(values: dict[str, Any]) -> ChatConfigAction:
        if "is_enabled" in values:
            return (
                ChatConfigAction.CHAT_ENABLED
                if values["is_enabled"]
                else ChatConfigAction.CHAT_DISABLED
            )
        return UPDATE_ACTIONS.get(next(iter(values)), ChatConfigAction.NOTIFICATION_CHANGED)

    async def set_responsible(
        self,
        telegram_chat_id: int,
        user_data: TelegramUserData,
        actor_id: int,
        *,
        thread_id: int | None = None,
    ) -> tuple[MonitoredChatRead, bool]:
        """Assign the responsible user. Returns ``(chat, added_as_responder)``."""
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, thread_id, for_update=True)
            user = await uow.users.upsert(user_data, self._clock.now())
            added = await self.assign_responsible(uow, chat, user, actor_id, source="menu")
            await uow.session.flush()
            result = MonitoredChatRead.model_validate(chat)
            await uow.commit()
        return result, added

    async def assign_responsible(
        self,
        uow: UnitOfWork,
        chat: MonitoredChat,
        user: TelegramUser,
        actor_id: int,
        *,
        source: str,
    ) -> bool:
        """Set the chat's responsible inside the caller's transaction (chat row locked).

        Affects new tickets only: existing tickets keep their responsible snapshot.
        Returns whether the user was auto-added to responders.
        """
        old_id = await self._responsible_tg_id(uow, chat)
        chat.responsible_user_id = user.id
        added = False
        if self._defaults.auto_add_responsible_as_responder:
            added = await uow.responders.add(chat.id, user.id, actor_id)
        await self._audit.record_chat_change(
            uow,
            chat_id=chat.id,
            actor_telegram_user_id=actor_id,
            action=ChatConfigAction.RESPONSIBLE_CHANGED,
            old_value={"responsible_telegram_id": old_id},
            new_value={
                "responsible_telegram_id": user.telegram_user_id,
                "auto_responder": added,
                "source": source,
            },
        )
        return added

    async def add_responder(
        self,
        telegram_chat_id: int,
        user_data: TelegramUserData,
        actor_id: int,
        *,
        thread_id: int | None = None,
    ) -> ResponderChange:
        return await self._change_responder(
            telegram_chat_id, user_data, actor_id, add=True, thread_id=thread_id
        )

    async def remove_responder(
        self,
        telegram_chat_id: int,
        user_data: TelegramUserData,
        actor_id: int,
        *,
        thread_id: int | None = None,
    ) -> ResponderChange:
        return await self._change_responder(
            telegram_chat_id, user_data, actor_id, add=False, thread_id=thread_id
        )

    async def _change_responder(
        self,
        telegram_chat_id: int,
        user_data: TelegramUserData,
        actor_id: int,
        *,
        add: bool,
        thread_id: int | None,
    ) -> ResponderChange:
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, thread_id)
            user = await uow.users.upsert(user_data, self._clock.now())
            if add:
                changed = await uow.responders.add(chat.id, user.id, actor_id)
            else:
                changed = await uow.responders.remove(chat.id, user.id)
            if changed:
                await self._audit.record_chat_change(
                    uow,
                    chat_id=chat.id,
                    actor_telegram_user_id=actor_id,
                    action=ChatConfigAction.RESPONDER_ADDED
                    if add
                    else ChatConfigAction.RESPONDER_REMOVED,
                    new_value={"telegram_user_id": user.telegram_user_id},
                )
            result = ResponderChange(changed, TelegramUserRead.model_validate(user))
            await uow.commit()
        return result

    async def migrate_chat(self, old_chat_id: int, new_chat_id: int) -> bool:
        """Follow a group -> supergroup upgrade (Telegram assigns a new chat ID)."""
        async with self._uow_factory() as uow:
            chats = await uow.chats.list_for_group(old_chat_id)
            if not chats or await uow.chats.list_for_group(new_chat_id):
                return False
            for chat in chats:
                old = {"telegram_chat_id": chat.telegram_chat_id, "chat_type": chat.chat_type.value}
                chat.telegram_chat_id = new_chat_id
                chat.chat_type = ChatType.SUPERGROUP
                await self._audit.record_chat_change(
                    uow,
                    chat_id=chat.id,
                    actor_telegram_user_id=0,
                    action=ChatConfigAction.CHAT_MIGRATED,
                    old_value=old,
                    new_value={
                        "telegram_chat_id": new_chat_id,
                        "chat_type": ChatType.SUPERGROUP.value,
                    },
                )
            await uow.commit()
        return True

    async def get_details(
        self, telegram_chat_id: int, *, thread_id: int | None = None
    ) -> MonitoredChatDetails | None:
        async with self._uow_factory() as uow:
            chat = await uow.chats.get_by_telegram_id(telegram_chat_id, thread_id=thread_id)
            return await self._details(uow, chat, full=False) if chat else None

    async def get_for_message(
        self, telegram_chat_id: int, thread_id: int | None
    ) -> MonitoredChatDetails | None:
        """The customer chat a message in this group/topic belongs to (topic first)."""
        async with self._uow_factory() as uow:
            chat = await uow.chats.get_for_message(telegram_chat_id, thread_id)
            return await self._details(uow, chat, full=False) if chat else None

    async def remember_topic(self, telegram_chat_id: int, thread_id: int, name: str) -> None:
        """Record a forum topic name (seen in messages); renames update topic chats."""
        key = (telegram_chat_id, thread_id)
        if self._known_topics.get(key) == name:
            return
        async with self._uow_factory() as uow:
            await uow.chats.remember_topic(telegram_chat_id, thread_id, name, self._clock.now())
            chat = await uow.chats.get_by_telegram_id(
                telegram_chat_id, thread_id=thread_id, for_update=True
            )
            if chat is not None and chat.topic_name != name:
                group = chat.title.rsplit(" / ", 1)[0] if chat.topic_name else chat.title
                chat.topic_name = name
                chat.title = topic_title(group, thread_id, name)
            await uow.commit()
        self._known_topics[key] = name

    async def topic_name(self, telegram_chat_id: int, thread_id: int) -> str | None:
        async with self._uow_factory() as uow:
            return await uow.chats.topic_name(telegram_chat_id, thread_id)

    async def topics(self, telegram_chat_id: int) -> list[tuple[int, str]]:
        async with self._uow_factory() as uow:
            return [(t.thread_id, t.name) for t in await uow.chats.topics(telegram_chat_id)]

    async def is_customer_chat(self, telegram_chat_id: int, thread_id: int | None) -> bool:
        async with self._uow_factory() as uow:
            return await uow.chats.get_for_message(telegram_chat_id, thread_id) is not None

    async def get_card(self, chat_id: int) -> MonitoredChatDetails | None:
        """Everything the settings menu shows for a chat (by internal ID)."""
        async with self._uow_factory() as uow:
            chat = await uow.chats.get(chat_id)
            return await self._details(uow, chat, full=True) if chat else None

    async def list_chats(self) -> list[MonitoredChatRead]:
        async with self._uow_factory() as uow:
            return [MonitoredChatRead.model_validate(c) for c in await uow.chats.list_all()]

    @staticmethod
    async def _details(uow: UnitOfWork, chat: MonitoredChat, *, full: bool) -> MonitoredChatDetails:
        responsible: TelegramUser | None = (
            await uow.users.get(chat.responsible_user_id) if chat.responsible_user_id else None
        )
        responders = await uow.responders.list_users(chat.id)
        members = await uow.members.list_users(chat.id) if full else []
        return MonitoredChatDetails(
            chat=MonitoredChatRead.model_validate(chat),
            responsible=TelegramUserRead.model_validate(responsible) if responsible else None,
            responders=tuple(TelegramUserRead.model_validate(u) for u in responders),
            members=tuple(TelegramUserRead.model_validate(u) for u in members),
            open_tickets=await uow.pending.count_open(chat.id) if full else 0,
            notification_error=await _delivery_error(uow, chat.id) if full else None,
        )

    async def record_pilot_invite(
        self,
        telegram_chat_id: int,
        actor_id: int,
        summary: dict[str, Any],
        *,
        thread_id: int | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, thread_id)
            await self._audit.record_chat_change(
                uow,
                chat_id=chat.id,
                actor_telegram_user_id=actor_id,
                action=ChatConfigAction.PILOT_INVITED,
                new_value=summary,
            )
            await uow.commit()

    async def clear_responsible(
        self, telegram_chat_id: int, actor_id: int, *, thread_id: int | None = None
    ) -> None:
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, thread_id, for_update=True)
            old_id = await self._responsible_tg_id(uow, chat)
            if old_id is None:
                return
            chat.responsible_user_id = None
            await self._audit.record_chat_change(
                uow,
                chat_id=chat.id,
                actor_telegram_user_id=actor_id,
                action=ChatConfigAction.RESPONSIBLE_CHANGED,
                old_value={"responsible_telegram_id": old_id},
                new_value={"responsible_telegram_id": None, "source": "menu"},
            )
            await uow.commit()

    @staticmethod
    async def _require(
        uow: UnitOfWork,
        telegram_chat_id: int,
        thread_id: int | None = None,
        *,
        for_update: bool = False,
    ) -> MonitoredChat:
        chat = await uow.chats.get_by_telegram_id(
            telegram_chat_id, thread_id=thread_id, for_update=for_update
        )
        if chat is None:
            raise ChatNotMonitoredError(telegram_chat_id)
        return chat

    @staticmethod
    async def _responsible_tg_id(uow: UnitOfWork, chat: MonitoredChat) -> int | None:
        if chat.responsible_user_id is None:
            return None
        user = await uow.users.get(chat.responsible_user_id)
        return user.telegram_user_id if user else None


async def _delivery_error(uow: UnitOfWork, chat_id: int) -> str | None:
    last = await uow.outbox.last_for_chat(chat_id)
    if last is None or last.status is not OutboxStatus.FAILED:
        return None
    return last.last_error or "неизвестная ошибка"
