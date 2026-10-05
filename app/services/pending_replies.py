from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.enums import PendingReplyStatus, ReplyEventType
from app.db.models import MonitoredChat, PendingReply, TelegramUser
from app.db.uow import UnitOfWork
from app.schemas.pending_replies import (
    IncomingMessage,
    MessageProcessingResult,
    PendingReplyCreate,
    PendingReplyRead,
)
from app.schemas.users import TelegramUserData
from app.services.clock import Clock
from app.services.message_links import MessageLinkService
from app.services.reply_matching import build_match_criteria, can_transition

logger = logging.getLogger(__name__)

MAX_SOURCE_TEXT_LENGTH = 4096


class NotRequiredOutcome(StrEnum):
    MARKED = "marked"
    ALREADY_FINAL = "already_final"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class NotRequiredResult:
    outcome: NotRequiredOutcome
    ticket: PendingReplyRead | None = None


class PendingReplyService:
    """Ticket lifecycle operations.

    ``create_in``/``close_in`` run inside a caller-owned unit of work so that message
    processing stays a single transaction.
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        links: MessageLinkService,
        *,
        responsible_can_mark_not_required: bool,
        merge_consecutive_messages: bool = True,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._links = links
        self._responsible_can_mark = responsible_can_mark_not_required
        self._merge_consecutive = merge_consecutive_messages

    async def create_in(
        self,
        uow: UnitOfWork,
        chat: MonitoredChat,
        author: TelegramUser,
        msg: IncomingMessage,
        now: datetime,
    ) -> MessageProcessingResult:
        if await uow.pending.message_tracked(chat.id, msg.message_id):
            return MessageProcessingResult(action="duplicate", reason="message already tracked")
        if self._merge_consecutive:
            await uow.pending.lock_author(chat.id, author.telegram_user_id)
            open_ticket = await uow.pending.find_open_by_author(
                chat.id, author.telegram_user_id, msg.message_thread_id
            )
            if open_ticket is not None and open_ticket.source_message_id < msg.message_id:
                return await self._merge(uow, open_ticket, author, msg)
        # SLA starts when the message was sent (capped by "now" against clock skew), so
        # updates delivered late after an outage do not get a fresh SLA.
        created_at = min(msg.date, now)
        responsible = (
            await uow.users.get(chat.responsible_user_id) if chat.responsible_user_id else None
        )
        data = PendingReplyCreate(
            chat_id=chat.id,
            source_message_id=msg.message_id,
            source_thread_id=msg.message_thread_id,
            source_author_user_id=author.id,
            source_author_telegram_id=author.telegram_user_id,
            source_author_name=msg.author.display_name,
            source_text=(msg.text or None) and msg.text[:MAX_SOURCE_TEXT_LENGTH],
            source_content_type=msg.content_type,
            source_message_date=msg.date,
            source_message_link=self._links.build(
                msg.telegram_chat_id,
                msg.message_id,
                thread_id=msg.message_thread_id if msg.is_topic_message else None,
                chat_username=msg.chat_username,
            ),
            created_at=created_at,
            deadline_at=created_at + timedelta(seconds=chat.sla_seconds),
            priority_snapshot=chat.priority,
            sla_seconds_snapshot=chat.sla_seconds,
            responsible_telegram_id_snapshot=responsible.telegram_user_id if responsible else None,
        )
        ticket_id = await uow.pending.create_if_absent(data)
        if ticket_id is None:
            return MessageProcessingResult(
                action="duplicate", reason="source message already tracked"
            )
        await uow.pending.add_message(chat.id, ticket_id, msg.message_id, msg.date)
        await uow.audit.add_reply_event(
            ticket_id,
            ReplyEventType.CREATED,
            actor_telegram_user_id=author.telegram_user_id,
            telegram_message_id=msg.message_id,
            metadata={"deadline_at": data.deadline_at.isoformat(), "sla_seconds": chat.sla_seconds},
        )
        return MessageProcessingResult(action="created", pending_reply_id=ticket_id)

    async def _merge(
        self, uow: UnitOfWork, ticket: PendingReply, author: TelegramUser, msg: IncomingMessage
    ) -> MessageProcessingResult:
        """Attach a follow-up message to the author's open ticket.

        The deadline stays anchored to the first message: the customer has been waiting
        since then, and one answer closes the whole batch.
        """
        if not await uow.pending.add_message(ticket.chat_id, ticket.id, msg.message_id, msg.date):
            return MessageProcessingResult(action="duplicate", reason="message already tracked")
        ticket.message_count += 1
        if ticket.last_message_id is None or msg.message_id > ticket.last_message_id:
            ticket.last_message_id = msg.message_id
            ticket.last_message_at = msg.date
            ticket.last_message_text = (msg.text or None) and msg.text[:MAX_SOURCE_TEXT_LENGTH]
            ticket.last_content_type = msg.content_type
        await uow.audit.add_reply_event(
            ticket.id,
            ReplyEventType.MESSAGE_ADDED,
            actor_telegram_user_id=author.telegram_user_id,
            telegram_message_id=msg.message_id,
            metadata={"message_count": ticket.message_count},
        )
        return MessageProcessingResult(action="merged", pending_reply_id=ticket.id)

    async def close_in(
        self,
        uow: UnitOfWork,
        chat: MonitoredChat,
        responder: TelegramUser,
        msg: IncomingMessage,
        now: datetime,
    ) -> MessageProcessingResult:
        if await uow.pending.response_already_used(chat.id, msg.message_id):
            return MessageProcessingResult(action="duplicate", reason="response already applied")
        criteria = build_match_criteria(
            chat.reply_match_mode,
            reply_to_message_id=msg.reply_to_message_id,
            message_thread_id=msg.message_thread_id,
            is_topic_message=msg.is_topic_message,
        )
        ticket = await uow.pending.find_for_answer(chat.id, criteria, msg.message_id)
        if ticket is None:
            return MessageProcessingResult(action="no_match")
        was_overdue = ticket.status is PendingReplyStatus.OVERDUE
        ticket.status = PendingReplyStatus.ANSWERED
        ticket.responded_at = min(msg.date, now)
        ticket.responded_by_user_id = responder.id
        ticket.responded_by_telegram_id = responder.telegram_user_id
        ticket.response_message_id = msg.message_id
        await uow.audit.add_reply_event(
            ticket.id,
            ReplyEventType.ANSWERED,
            actor_telegram_user_id=responder.telegram_user_id,
            telegram_message_id=msg.message_id,
            metadata={"was_overdue": was_overdue, "match": criteria.kind.value},
        )
        return MessageProcessingResult(action="answered", pending_reply_id=ticket.id)

    async def mark_not_required(
        self, pending_reply_id: int, actor: TelegramUserData, *, is_admin: bool
    ) -> NotRequiredResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            # Blocking lock: simultaneous presses are serialized; the second one sees
            # the final status and leaves the ticket untouched.
            ticket = await uow.pending.get_for_update(pending_reply_id)
            if ticket is None:
                return NotRequiredResult(NotRequiredOutcome.NOT_FOUND)
            if not is_admin and not await self._is_responsible(uow, ticket, actor.telegram_user_id):
                return NotRequiredResult(NotRequiredOutcome.FORBIDDEN)
            if not can_transition(ticket.status, PendingReplyStatus.NOT_REQUIRED):
                return NotRequiredResult(
                    NotRequiredOutcome.ALREADY_FINAL, PendingReplyRead.model_validate(ticket)
                )
            user = await uow.users.upsert(actor, now)
            previous = ticket.status
            ticket.status = PendingReplyStatus.NOT_REQUIRED
            ticket.not_required_at = now
            ticket.not_required_by_user_id = user.id
            ticket.not_required_by_telegram_id = actor.telegram_user_id
            await uow.audit.add_reply_event(
                ticket.id,
                ReplyEventType.NOT_REQUIRED,
                actor_telegram_user_id=actor.telegram_user_id,
                metadata={"previous_status": previous.value, "by_admin": is_admin},
            )
            await uow.session.flush()
            result = PendingReplyRead.model_validate(ticket)
            await uow.commit()
        return NotRequiredResult(NotRequiredOutcome.MARKED, result)

    async def _is_responsible(self, uow: UnitOfWork, ticket: PendingReply, actor_id: int) -> bool:
        if not self._responsible_can_mark:
            return False
        if ticket.responsible_telegram_id_snapshot == actor_id:
            return True
        chat = await uow.chats.get(ticket.chat_id)
        if chat is None or chat.responsible_user_id is None:
            return False
        responsible = await uow.users.get(chat.responsible_user_id)
        return responsible is not None and responsible.telegram_user_id == actor_id

    async def list_open(self, chat_id: int, limit: int = 50) -> list[PendingReplyRead]:
        """Open tickets of a monitored chat (internal ID)."""
        async with self._uow_factory() as uow:
            rows = await uow.pending.list_open(chat_id, limit)
            return [PendingReplyRead.model_validate(r) for r in rows]
