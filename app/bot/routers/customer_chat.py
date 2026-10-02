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
from aiogram.types import Message

from app.bot.extractors import GROUP_TYPES
from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.invites import format_invite_report, invite_pilot
from app.bot.menus import chat_screen
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
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
    created, chat = await services.chats.add_chat(
        message.chat.id, message.chat.title or "", ChatType(message.chat.type), actor.id
    )
    await _try_delete(message)
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
    recipients = [actor.id]
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
    if not delivered_to_actor:
        # The admin has not started the bot yet: leave a short hint in the group.
        await message.answer(GROUP_HINT)


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
    details = await services.chats.get_details(message.chat.id)
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
