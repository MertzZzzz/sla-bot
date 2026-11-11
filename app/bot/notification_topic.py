"""Default notification forum: a newly added chat gets its own topic there.

The forum group is chosen in /menu → «🔔 Уведомления по умолчанию». When a chat is
connected, the bot creates a topic named like the chat in that group and routes the
chat's notifications into it. The bot needs the "manage topics" admin right there; if
the topic cannot be created, notifications go to the group's general chat instead.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from app.bot.services import BotServices
from app.schemas.chats import MonitoredChatRead, MonitoredChatUpdate
from app.services.formatting import escape

logger = logging.getLogger(__name__)

TOPIC_NAME_LIMIT = 128  # Bot API limit for forum topic names


def topic_name_for(chat: MonitoredChatRead) -> str:
    return chat.title[:TOPIC_NAME_LIMIT]


async def route_to_new_topic(
    bot: Bot, services: BotServices, chat: MonitoredChatRead, actor_id: int
) -> str | None:
    """Create a topic for a newly added chat in the default forum and send notifications
    there. Returns a line for the admin (``None`` when no default forum is configured or
    the chat already has a notification target)."""
    target = await services.bot_settings.default_notification()
    if target is None or chat.notification_chat_id is not None:
        return None
    name = topic_name_for(chat)
    group = escape(target.title)
    try:
        topic = await bot.create_forum_topic(chat_id=target.chat_id, name=name)
    except TelegramAPIError as exc:
        logger.warning(
            "cannot create notification topic",
            extra={
                "event": "notification_topic_failed",
                "telegram_chat_id": target.chat_id,
                "error_type": type(exc).__name__,
            },
        )
        await _set_target(services, chat, target.chat_id, None, actor_id)
        return (
            f"⚠️ Не удалось создать топик в «{group}»: <i>{escape(exc.message)}</i>. "
            "Уведомления пойдут в общий чат этой группы. Проверьте, что в группе включены "
            "темы и у бота есть право «Управление темами»."
        )
    thread_id = topic.message_thread_id
    await services.chats.remember_topic(target.chat_id, thread_id, name)
    await _set_target(services, chat, target.chat_id, thread_id, actor_id)
    return f"🔔 Уведомления: новый топик «{escape(name)}» в «{group}»."


async def _set_target(
    services: BotServices,
    chat: MonitoredChatRead,
    chat_id: int,
    thread_id: int | None,
    actor_id: int,
) -> None:
    await services.chats.update(
        chat.telegram_chat_id,
        MonitoredChatUpdate(notification_chat_id=chat_id, notification_thread_id=thread_id),
        actor_id,
        thread_id=chat.thread_id,
    )


async def topic_rights_problem(bot: Bot, chat_id: int) -> str | None:
    """Why the bot cannot create topics in this group (``None`` if it can)."""
    me = await bot.me()
    try:
        member = await bot.get_chat_member(chat_id, me.id)
    except TelegramAPIError:
        return "Бот не состоит в этой группе — добавьте его туда."
    if member.status == ChatMemberStatus.CREATOR:
        return None
    if member.status == ChatMemberStatus.ADMINISTRATOR and getattr(
        member, "can_manage_topics", False
    ):
        return None
    return (
        "У бота нет права «Управление темами» в этой группе: сделайте бота администратором "
        "с этим правом, иначе уведомления новых чатов пойдут в общий чат группы."
    )
