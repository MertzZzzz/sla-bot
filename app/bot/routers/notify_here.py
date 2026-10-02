"""/notify_here: route a chat's SLA notifications into the current group or topic.

Works in any group the bot is in (except customer chats), so the notification group
does not need to be listed in APP_TELEGRAM__ADMIN_CHAT_IDS. Also answers /menu in such
groups with a hint instead of staying silent.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.extractors import GROUP_TYPES
from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.services import BotServices
from app.core.config import Settings
from app.schemas.callbacks import NotifyHereCallbackData
from app.schemas.chats import MonitoredChatUpdate
from app.services.formatting import escape, truncate

router = Router(name="notify_here")
router.message.filter(F.chat.type.in_(GROUP_TYPES))

MAX_BUTTONS = 50
MENU_HINT = (
    "Меню настроек в этой группе недоступно.\n"
    "• Чтобы уведомления приходили сюда — напишите здесь /notify_here.\n"
    "• Чтобы открывать меню здесь — добавьте ID группы <code>{chat_id}</code> в "
    "APP_TELEGRAM__ADMIN_CHAT_IDS.\n"
    "• Или откройте меню в личном чате с ботом: /menu."
)


def _thread(message: Message) -> int | None:
    return message.message_thread_id if message.is_topic_message else None


async def _is_customer_chat(services: BotServices, chat_id: int) -> bool:
    return await services.chats.get_details(chat_id) is not None


@router.message(Command("notify_here"), IsGlobalAdmin())
async def cmd_notify_here(message: Message, services: BotServices) -> None:
    if await _is_customer_chat(services, message.chat.id):
        return  # never configure from (or expose settings in) a customer chat
    chats = await services.chats.list_chats()
    if not chats:
        await message.reply("Чатов заказчиков пока нет: сначала выполните /chat_add в их группах.")
        return
    thread = _thread(message)
    rows = []
    for chat in chats[:MAX_BUTTONS]:
        here = chat.notification_chat_id == message.chat.id and (
            chat.notification_thread_id == thread
        )
        label = ("✓ " if here else "") + truncate(chat.title, 50)
        data = NotifyHereCallbackData(chat=chat.id).pack()
        rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    where = "этот топик" if thread else "эту группу"
    await message.reply(
        f"🔔 Уведомления какого чата заказчика направлять в {where}? ✓ — уже сюда.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(NotifyHereCallbackData.filter())
async def on_notify_here(
    callback: CallbackQuery, callback_data: NotifyHereCallbackData, services: BotServices
) -> None:
    message = callback.message
    if not isinstance(message, Message) or not await services.admins.is_admin(
        callback.from_user.id
    ):
        await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        return
    details = await services.chats.get_card(callback_data.chat)
    if details is None:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    thread = _thread(message)
    await services.chats.update(
        details.chat.telegram_chat_id,
        MonitoredChatUpdate(notification_chat_id=message.chat.id, notification_thread_id=thread),
        callback.from_user.id,
    )
    await callback.answer("Готово")
    topic = " (в этот топик)" if thread else ""
    await message.edit_text(
        f"✅ Уведомления о нарушении SLA в чате «{escape(details.chat.title)}» "
        f"будут приходить сюда{topic}."
    )


@router.message(Command("menu", "start"), IsGlobalAdmin())
async def menu_hint(message: Message, services: BotServices, settings: Settings) -> None:
    # Reached only when the menu router declined: not an admin chat. Stay silent in
    # customer chats; elsewhere explain instead of ignoring the admin.
    if settings.is_admin_chat(message.chat.id) or await _is_customer_chat(
        services, message.chat.id
    ):
        return
    await message.reply(MENU_HINT.format(chat_id=message.chat.id))
