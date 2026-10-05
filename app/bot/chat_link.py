"""A link that opens a customer chat from an SLA notification.

Public chats have ``t.me/<username>`` (``/<topic>`` for a forum topic); a private forum
topic links as ``t.me/c/<id>/<topic>`` (opens for members). For other private chats the
bot creates its own invite link with join requests: chat members simply open the chat,
outsiders can only ask to join (an admin must approve). Requires the bot to be an admin
allowed to invite users; without it notifications fall back to the message link.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot.services import BotServices
from app.schemas.chats import MonitoredChatRead
from app.services.chat_settings import public_link

logger = logging.getLogger(__name__)

LINK_NAME = "SLA-бот: уведомления"


async def ensure_chat_link(bot: Bot, services: BotServices, chat: MonitoredChatRead) -> str | None:
    if chat.chat_link:
        return chat.chat_link
    try:
        info = await bot.get_chat(chat.telegram_chat_id)
        if info.username:
            link = public_link(info.username, chat.thread_id)
        elif chat.thread_id:
            # Topics live in supergroups: t.me/c/<id>/<topic> opens the topic for members.
            internal_id = -chat.telegram_chat_id - 1_000_000_000_000
            link = f"https://t.me/c/{internal_id}/{chat.thread_id}"
        else:
            invite = await bot.create_chat_invite_link(
                chat.telegram_chat_id, name=LINK_NAME, creates_join_request=True
            )
            link = invite.invite_link
    except TelegramAPIError as exc:
        logger.info(
            "cannot create chat link",
            extra={
                "event": "chat_link_unavailable",
                "telegram_chat_id": chat.telegram_chat_id,
                "error_type": type(exc).__name__,
            },
        )
        return None
    await services.chats.set_chat_link(chat.id, link)
    return link
