"""Changing the responsible user from an SLA notification.

Two scopes:

* ``TICKET`` — the responsible of this one message (the ticket's snapshot). Allowed for
  global admins and for the ticket's current responsible (hand-off). Statistics follow
  the ticket, so the breach is attributed to the new responsible.
* ``CHAT`` — the chat's responsible for *future* messages (same as /chat_responsible).
  Global admins only; existing tickets keep their snapshot.

Candidates are the chat's responders; a user not in that list is rejected even if a
forged callback carries their ID.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.enums import PendingReplyStatus, ReplyEventType
from app.db.models import MonitoredChat, PendingReply, TelegramUser
from app.db.uow import UnitOfWork
from app.schemas.users import TelegramUserData, TelegramUserRead
from app.services.chat_settings import ChatSettingsService
from app.services.reply_matching import can_transition


class ReassignScope(StrEnum):
    TICKET = "ticket"
    CHAT = "chat"


class ReassignOutcome(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    FINAL = "final"
    NO_CANDIDATES = "no_candidates"
    INVALID_USER = "invalid_user"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ReassignOptions:
    outcome: ReassignOutcome
    candidates: list[TelegramUserRead] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReassignResult:
    outcome: ReassignOutcome
    old: TelegramUserRead | None = None
    new: TelegramUserRead | None = None
    status: PendingReplyStatus | None = None


def _read(user: TelegramUser | None) -> TelegramUserRead | None:
    return TelegramUserRead.model_validate(user) if user else None


class ReassignmentService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], chat_settings: ChatSettingsService
    ) -> None:
        self._uow_factory = uow_factory
        self._chat_settings = chat_settings

    async def options(
        self, pending_reply_id: int, scope: ReassignScope, actor_id: int, *, is_admin: bool
    ) -> ReassignOptions:
        async with self._uow_factory() as uow:
            ticket = await uow.pending.get(pending_reply_id)
            if ticket is None:
                return ReassignOptions(ReassignOutcome.NOT_FOUND)
            chat = await uow.chats.get(ticket.chat_id)
            assert chat is not None
            denied = await self._check(uow, ticket, chat, scope, actor_id, is_admin=is_admin)
            if denied is not None:
                return ReassignOptions(denied)
            current = await self._current_id(uow, ticket, chat, scope)
            candidates = [
                TelegramUserRead.model_validate(u)
                for u in await uow.responders.list_users(chat.id)
                if u.telegram_user_id != current
            ]
        if not candidates:
            return ReassignOptions(ReassignOutcome.NO_CANDIDATES)
        return ReassignOptions(ReassignOutcome.OK, candidates)

    async def reassign(
        self,
        pending_reply_id: int,
        scope: ReassignScope,
        new_telegram_id: int,
        actor: TelegramUserData,
        *,
        is_admin: bool,
    ) -> ReassignResult:
        async with self._uow_factory() as uow:
            ticket = await uow.pending.get_for_update(pending_reply_id)
            if ticket is None:
                return ReassignResult(ReassignOutcome.NOT_FOUND)
            chat = await uow.chats.get(ticket.chat_id, for_update=scope is ReassignScope.CHAT)
            assert chat is not None
            denied = await self._check(
                uow, ticket, chat, scope, actor.telegram_user_id, is_admin=is_admin
            )
            if denied is not None:
                return ReassignResult(denied, status=ticket.status)
            new = await uow.responders.get_responder(chat.id, new_telegram_id)
            if new is None:
                return ReassignResult(ReassignOutcome.INVALID_USER)
            current = await self._current_id(uow, ticket, chat, scope)
            old = await uow.users.get_by_telegram_id(current) if current is not None else None
            if current == new_telegram_id:
                return ReassignResult(ReassignOutcome.UNCHANGED, _read(old), _read(new))
            if scope is ReassignScope.TICKET:
                await self._reassign_ticket(uow, ticket, current, new, actor)
            else:
                await self._chat_settings.assign_responsible(
                    uow, chat, new, actor.telegram_user_id, source="notification"
                )
            result = ReassignResult(ReassignOutcome.OK, _read(old), _read(new), ticket.status)
            await uow.commit()
        return result

    @staticmethod
    async def _reassign_ticket(
        uow: UnitOfWork,
        ticket: PendingReply,
        current: int | None,
        new: TelegramUser,
        actor: TelegramUserData,
    ) -> None:
        ticket.responsible_telegram_id_snapshot = new.telegram_user_id
        await uow.audit.add_reply_event(
            ticket.id,
            ReplyEventType.REASSIGNED,
            actor_telegram_user_id=actor.telegram_user_id,
            metadata={"from_telegram_id": current, "to_telegram_id": new.telegram_user_id},
        )

    async def _check(
        self,
        uow: UnitOfWork,
        ticket: PendingReply,
        chat: MonitoredChat,
        scope: ReassignScope,
        actor_id: int,
        *,
        is_admin: bool,
    ) -> ReassignOutcome | None:
        if scope is ReassignScope.CHAT:
            return None if is_admin else ReassignOutcome.FORBIDDEN
        if not is_admin and actor_id not in {
            ticket.responsible_telegram_id_snapshot,
            await self._chat_responsible_id(uow, chat),
        }:
            return ReassignOutcome.FORBIDDEN
        # Only open tickets can be handed off; closed ones are history.
        if not can_transition(ticket.status, PendingReplyStatus.ANSWERED):
            return ReassignOutcome.FINAL
        return None

    async def _current_id(
        self, uow: UnitOfWork, ticket: PendingReply, chat: MonitoredChat, scope: ReassignScope
    ) -> int | None:
        if scope is ReassignScope.TICKET:
            return ticket.responsible_telegram_id_snapshot
        return await self._chat_responsible_id(uow, chat)

    @staticmethod
    async def _chat_responsible_id(uow: UnitOfWork, chat: MonitoredChat) -> int | None:
        if chat.responsible_user_id is None:
            return None
        user = await uow.users.get(chat.responsible_user_id)
        return user.telegram_user_id if user else None
