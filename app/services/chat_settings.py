from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import AppSettings
from app.core.enums import ChatConfigAction, ChatType
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


def _jsonable(chat: MonitoredChat, fields: set[str]) -> dict[str, Any]:
    return MonitoredChatRead.model_validate(chat).model_dump(mode="json", include=fields)


class ChatSettingsService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        audit: AuditService,
        defaults: AppSettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._audit = audit
        self._defaults = defaults

    async def add_chat(
        self, telegram_chat_id: int, title: str, chat_type: ChatType, actor_id: int
    ) -> tuple[bool, MonitoredChatRead]:
        """Start monitoring a chat with default settings. Returns ``(created, chat)``."""
        data = MonitoredChatCreate(
            telegram_chat_id=telegram_chat_id,
            title=title[:256] or str(telegram_chat_id),
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
                chat = await self._require(uow, telegram_chat_id)
            else:
                await self._audit.record_chat_change(
                    uow,
                    chat_id=chat.id,
                    actor_telegram_user_id=actor_id,
                    action=ChatConfigAction.CHAT_ADDED,
                    new_value=data.model_dump(mode="json"),
                )
            result = MonitoredChatRead.model_validate(chat)
            await uow.commit()
        return created, result

    async def update(
        self, telegram_chat_id: int, changes: MonitoredChatUpdate, actor_id: int
    ) -> MonitoredChatRead:
        values = changes.model_dump(exclude_unset=True)
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, for_update=True)
            fields = set(values)
            old = _jsonable(chat, fields)
            updated = await uow.chats.update_fields(chat.id, values)
            new = _jsonable(updated, fields)
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
    def _action_for(values: dict[str, Any]) -> ChatConfigAction:
        if "is_enabled" in values:
            return (
                ChatConfigAction.CHAT_ENABLED
                if values["is_enabled"]
                else ChatConfigAction.CHAT_DISABLED
            )
        return UPDATE_ACTIONS.get(next(iter(values)), ChatConfigAction.NOTIFICATION_CHANGED)

    async def set_responsible(
        self, telegram_chat_id: int, user_data: TelegramUserData, actor_id: int
    ) -> tuple[MonitoredChatRead, bool]:
        """Assign the responsible user. Returns ``(chat, added_as_responder)``."""
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id, for_update=True)
            user = await uow.users.upsert(user_data, self._clock.now())
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
                },
            )
            await uow.session.flush()
            result = MonitoredChatRead.model_validate(chat)
            await uow.commit()
        return result, added

    async def add_responder(
        self, telegram_chat_id: int, user_data: TelegramUserData, actor_id: int
    ) -> ResponderChange:
        return await self._change_responder(telegram_chat_id, user_data, actor_id, add=True)

    async def remove_responder(
        self, telegram_chat_id: int, user_data: TelegramUserData, actor_id: int
    ) -> ResponderChange:
        return await self._change_responder(telegram_chat_id, user_data, actor_id, add=False)

    async def _change_responder(
        self, telegram_chat_id: int, user_data: TelegramUserData, actor_id: int, *, add: bool
    ) -> ResponderChange:
        async with self._uow_factory() as uow:
            chat = await self._require(uow, telegram_chat_id)
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
            chat = await uow.chats.get_by_telegram_id(old_chat_id, for_update=True)
            if chat is None or await uow.chats.get_by_telegram_id(new_chat_id) is not None:
                return False
            old = {"telegram_chat_id": chat.telegram_chat_id, "chat_type": chat.chat_type.value}
            chat.telegram_chat_id = new_chat_id
            chat.chat_type = ChatType.SUPERGROUP
            await self._audit.record_chat_change(
                uow,
                chat_id=chat.id,
                actor_telegram_user_id=0,
                action=ChatConfigAction.CHAT_MIGRATED,
                old_value=old,
                new_value={"telegram_chat_id": new_chat_id, "chat_type": ChatType.SUPERGROUP.value},
            )
            await uow.commit()
        return True

    async def get_details(self, telegram_chat_id: int) -> MonitoredChatDetails | None:
        async with self._uow_factory() as uow:
            chat = await uow.chats.get_by_telegram_id(telegram_chat_id)
            if chat is None:
                return None
            responsible: TelegramUser | None = (
                await uow.users.get(chat.responsible_user_id) if chat.responsible_user_id else None
            )
            responders = await uow.responders.list_users(chat.id)
            return MonitoredChatDetails(
                chat=MonitoredChatRead.model_validate(chat),
                responsible=TelegramUserRead.model_validate(responsible) if responsible else None,
                responders=tuple(TelegramUserRead.model_validate(u) for u in responders),
            )

    @staticmethod
    async def _require(
        uow: UnitOfWork, telegram_chat_id: int, *, for_update: bool = False
    ) -> MonitoredChat:
        chat = await uow.chats.get_by_telegram_id(telegram_chat_id, for_update=for_update)
        if chat is None:
            raise ChatNotMonitoredError(telegram_chat_id)
        return chat

    @staticmethod
    async def _responsible_tg_id(uow: UnitOfWork, chat: MonitoredChat) -> int | None:
        if chat.responsible_user_id is None:
            return None
        user = await uow.users.get(chat.responsible_user_id)
        return user.telegram_user_id if user else None
