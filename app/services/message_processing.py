from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError

from app.db.uow import UnitOfWork
from app.schemas.pending_replies import IncomingMessage, MessageProcessingResult
from app.services.chat_settings import public_link, topic_title
from app.services.clock import Clock
from app.services.pending_replies import PendingReplyService

logger = logging.getLogger(__name__)


class MessageProcessingService:
    """Decides whether a group message opens a ticket or answers one."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        pending: PendingReplyService,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._pending = pending
        self._clock = clock

    async def process(self, msg: IncomingMessage) -> MessageProcessingResult:
        now = self._clock.now()
        try:
            async with self._uow_factory() as uow:
                topic = msg.message_thread_id if msg.is_topic_message else None
                chat = await uow.chats.get_for_message(msg.telegram_chat_id, topic)
                if chat is None:
                    return MessageProcessingResult(action="ignored", reason="chat not monitored")
                if not chat.is_enabled:
                    return MessageProcessingResult(action="ignored", reason="monitoring disabled")
                title = (
                    topic_title(msg.chat_title, chat.thread_id, chat.topic_name)
                    if chat.thread_id
                    else msg.chat_title
                )
                if chat.title != title:
                    chat.title = title[:256]
                if msg.chat_username and chat.chat_username != msg.chat_username:
                    # Became public (or renamed): the public link is the best one.
                    chat.chat_username = msg.chat_username
                    chat.chat_link = public_link(msg.chat_username, chat.thread_id)
                user = await uow.users.upsert(msg.author, now)
                await uow.members.touch(chat.id, user.id, now)
                if await uow.responders.is_responder(chat.id, user.id):
                    result = await self._pending.close_in(uow, chat, user, msg, now)
                else:
                    result = await self._pending.create_in(uow, chat, user, msg, now)
                await uow.commit()
        except IntegrityError:
            # A concurrent delivery of the same update won the race on a unique index.
            logger.info(
                "duplicate message processing",
                extra={
                    "event": "message_duplicate_race",
                    "update_id": msg.update_id,
                    "telegram_chat_id": msg.telegram_chat_id,
                    "telegram_message_id": msg.message_id,
                },
            )
            return MessageProcessingResult(action="duplicate", reason="concurrent update")
        logger.info(
            "message processed",
            extra={
                "event": f"message_{result.action}",
                "update_id": msg.update_id,
                "telegram_chat_id": msg.telegram_chat_id,
                "telegram_message_id": msg.message_id,
                "telegram_user_id": msg.author.telegram_user_id,
                "pending_reply_id": result.pending_reply_id,
            },
        )
        return result
