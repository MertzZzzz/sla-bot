"""Customer (monitored) group chats: only /chat_add and /invite_all (admins).

Everything else is configured in a private chat with the bot or in an admin chat, so
the customer never sees configuration traffic. Other commands are silently ignored.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message, User

from app.bot.chat_link import ensure_chat_link
from app.bot.extractors import GROUP_TYPES, topic_of
from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.invites import format_invite_report, invite_pilot
from app.bot.menus import chat_screen
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
from app.schemas.chats import MonitoredChatRead
from app.services.formatting import escape

logger = logging.getLogger(__name__)
router = Router(name="customer_chat")

GROUP_HINT = "✅ Чат подключён. Чтобы настроить его, откройте личный чат с ботом и нажмите /start."


@router.message(Command("chat_add"), IsGlobalAdmin())
async def cmd_chat_add(
    message: Message, services: BotServices, settings: Settings, bot: Bot
) -> None:
    assert message.from_user is not None
    if message.chat.type not in GROUP_TYPES or settings.is_admin_chat(message.chat.id):
        await message.reply(
            "Команду /chat_add выполняют в группе заказчика, которую нужно отслеживать."
        )
        return
    actor = message.from_user
    thread_id, topic_name = await _topic(message, services)
    created, chat = await services.chats.add_chat(
        message.chat.id,
        message.chat.title or "",
        ChatType(message.chat.type),
        actor.id,
        username=message.chat.username,
        thread_id=thread_id,
        topic_name=topic_name,
    )
    await _try_delete(message)
    delivered = await announce_chat(bot, services, chat, actor, created=created)
    if not delivered:
        # The admin has not started the bot yet: leave a short hint in the group.
        await message.answer(GROUP_HINT)


async def _topic(message: Message, services: BotServices) -> tuple[int | None, str | None]:
    """/chat_add inside a forum topic monitors only that topic."""
    topic = topic_of(message)
    if topic is None:
        return None, None
    thread_id, name = topic
    if name:
        await services.chats.remember_topic(message.chat.id, thread_id, name)
    return thread_id, name or await services.chats.topic_name(message.chat.id, thread_id)


async def announce_chat(
    bot: Bot,
    services: BotServices,
    chat: MonitoredChatRead,
    actor: User,
    *,
    created: bool,
    send_to_actor: bool = True,
) -> bool:
    """Send the chat card to the admin (and others for new chats); True if the admin got it."""
    await ensure_chat_link(bot, services, chat)
    details = await services.chats.get_card(chat.id)
    assert details is not None
    who = escape(actor.full_name)
    notice = (
        f"🆕 <b>Новый чат подключён к мониторингу</b> (подключил {who}). "
        "Проверьте настройки ниже — особенно ответственного, отвечающих и уведомления."
        if created
        else "ℹ️ Этот чат уже подключён. Текущие настройки:"
    )
    screen = chat_screen(details, notice=notice)
    recipients = [actor.id] if send_to_actor else []
    if created:
        recipients += [a for a in await services.admins.new_chat_recipients() if a != actor.id]
    delivered_to_actor = False
    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, screen.text, reply_markup=screen.markup)
            delivered_to_actor = delivered_to_actor or admin_id == actor.id
        except TelegramAPIError as exc:
            logger.info(
                "cannot deliver chat card to admin",
                extra={
                    "event": "chat_card_undelivered",
                    "telegram_user_id": admin_id,
                    "error_type": type(exc).__name__,
                },
            )
    return delivered_to_actor


async def _try_delete(message: Message) -> None:
    """Keep the customer chat clean; needs the "delete messages" admin right."""
    with contextlib.suppress(TelegramAPIError):
        await message.delete()


INVITE_HINT = "Чтобы получить отчёт о приглашениях, откройте личный чат с ботом и нажмите /start."


@router.message(Command("invite_all"), IsGlobalAdmin())
async def cmd_invite_all(
    message: Message, services: BotServices, settings: Settings, bot: Bot
) -> None:
    """Invite pilot participants into this chat; the report goes to the admin privately."""
    assert message.from_user is not None
    if message.chat.type not in GROUP_TYPES or settings.is_admin_chat(message.chat.id):
        return
    await _try_delete(message)
    actor = message.from_user.id
    topic = topic_of(message)
    details = await services.chats.get_for_message(message.chat.id, topic[0] if topic else None)
    if details is None:
        chunks = ["Этот чат ещё не подключён: сначала выполните в нём /chat_add."]
    else:
        report = await invite_pilot(bot, services, details, actor, datetime.now(UTC))
        chunks = format_invite_report(report)
    try:
        for chunk in chunks:
            await bot.send_message(actor, chunk)
    except TelegramAPIError:
        # Never post invite links into the customer chat.
        await message.answer(INVITE_HINT)
