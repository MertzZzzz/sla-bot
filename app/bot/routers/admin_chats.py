"""Chat configuration commands.

Behaviour (documented in README): configuration commands are executed *inside the
monitored group* and always act on that group; only global admins may use them.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject, ExceptionTypeFilter
from aiogram.types import ErrorEvent, Message, User

from app.bot.extractors import user_data
from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.filters.is_monitored_chat import IsGroupChat, IsMonitoredChat
from app.bot.services import BotServices
from app.bot.texts import (
    GROUP_ONLY,
    NOT_ADMIN,
    NOT_MONITORED,
    REPLY_REQUIRED,
    REPLY_TO_BOT,
    chat_settings_text,
    responders_text,
    user_label,
)
from app.core.enums import ChatType
from app.schemas.chats import MonitoredChatDetails, MonitoredChatUpdate
from app.schemas.commands import (
    ChangeModeCommand,
    ChangePriorityCommand,
    ChangeSlaCommand,
    ChangeTimezoneCommand,
    CommandArgumentError,
    SetNotificationCommand,
)
from app.services.chat_settings import ChatNotMonitoredError
from app.services.formatting import format_duration

router = Router(name="admin_chats")

ADMIN_COMMANDS = (
    "chat_add",
    "chat_disable",
    "chat_enable",
    "chat_settings",
    "chat_sla",
    "chat_priority",
    "chat_mode",
    "chat_timezone",
    "chat_responsible",
    "chat_add_responder",
    "chat_remove_responder",
    "chat_responders",
    "chat_notification",
)


def _actor(message: Message) -> int:
    assert message.from_user is not None
    return message.from_user.id


def _reply_target(message: Message) -> User | None:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        return None
    # A plain message inside a forum topic "replies" to the topic root; that is not a target.
    if message.is_topic_message and reply.message_id == message.message_thread_id:
        return None
    return reply.from_user


@router.message(Command(*ADMIN_COMMANDS), ~IsGlobalAdmin())
async def refuse_non_admin(message: Message) -> None:
    await message.reply(NOT_ADMIN)


@router.message(Command(*ADMIN_COMMANDS), ~IsGroupChat())
async def refuse_outside_group(message: Message) -> None:
    await message.reply(GROUP_ONLY)


@router.message(Command("chat_add"))
async def cmd_chat_add(message: Message, services: BotServices) -> None:
    created, chat = await services.chats.add_chat(
        message.chat.id, message.chat.title or "", ChatType(message.chat.type), _actor(message)
    )
    if created:
        await message.reply(
            f"✅ Чат добавлен в мониторинг. SLA {format_duration(chat.sla_seconds)}, "
            f"приоритет {chat.priority.label}.\n"
            "Дальше: /chat_responsible, /chat_add_responder и /chat_notification."
        )
    else:
        await message.reply("Чат уже отслеживается. /chat_settings — текущие настройки.")


@router.message(Command(*ADMIN_COMMANDS[1:]), ~IsMonitoredChat())
async def refuse_not_monitored(message: Message) -> None:
    await message.reply(NOT_MONITORED)


async def _update(
    message: Message, services: BotServices, changes: MonitoredChatUpdate, done: str
) -> None:
    await services.chats.update(message.chat.id, changes, _actor(message))
    await message.reply(done)


@router.message(Command("chat_enable"))
async def cmd_enable(message: Message, services: BotServices) -> None:
    await _update(message, services, MonitoredChatUpdate(is_enabled=True), "✅ Мониторинг включён.")


@router.message(Command("chat_disable"))
async def cmd_disable(message: Message, services: BotServices) -> None:
    await _update(
        message,
        services,
        MonitoredChatUpdate(is_enabled=False),
        "⏸ Мониторинг выключен. История сохранена, открытые ожидания отменены.",
    )


@router.message(Command("chat_settings"), IsMonitoredChat())
async def cmd_settings(message: Message, monitored: MonitoredChatDetails) -> None:
    await message.reply(chat_settings_text(monitored))


@router.message(Command("chat_responders"), IsMonitoredChat())
async def cmd_responders(message: Message, monitored: MonitoredChatDetails) -> None:
    await message.reply(responders_text(monitored))


@router.message(Command("chat_sla"))
async def cmd_sla(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = ChangeSlaCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    await _update(
        message,
        services,
        MonitoredChatUpdate(sla_seconds=args.sla_seconds),
        f"✅ SLA: {format_duration(args.sla_seconds)}. Действует для новых сообщений.",
    )


@router.message(Command("chat_priority"))
async def cmd_priority(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = ChangePriorityCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    await _update(
        message,
        services,
        MonitoredChatUpdate(priority=args.priority),
        f"✅ Приоритет: {args.priority.label}.",
    )


@router.message(Command("chat_mode"))
async def cmd_mode(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = ChangeModeCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    await _update(
        message,
        services,
        MonitoredChatUpdate(reply_match_mode=args.mode),
        f"✅ Режим сопоставления: <code>{args.mode.value}</code>.",
    )


@router.message(Command("chat_timezone"))
async def cmd_timezone(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = ChangeTimezoneCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    await _update(
        message,
        services,
        MonitoredChatUpdate(timezone=args.timezone),
        f"✅ Часовой пояс: {args.timezone}.",
    )


@router.message(Command("chat_notification"))
async def cmd_notification(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = SetNotificationCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    changes = MonitoredChatUpdate(
        notification_chat_id=args.notification_chat_id,
        notification_thread_id=args.notification_thread_id,
    )
    topic = f", topic {args.notification_thread_id}" if args.notification_thread_id else ""
    await _update(
        message,
        services,
        changes,
        f"✅ Уведомления: чат <code>{args.notification_chat_id}</code>{topic}.\n"
        "Убедитесь, что бот добавлен в этот чат и может в нём писать.",
    )


@router.message(Command("chat_responsible"))
async def cmd_responsible(message: Message, services: BotServices) -> None:
    target = _reply_target(message)
    if target is None:
        await message.reply(REPLY_REQUIRED)
        return
    if target.is_bot:
        await message.reply(REPLY_TO_BOT)
        return
    _, added = await services.chats.set_responsible(
        message.chat.id, user_data(target), _actor(message)
    )
    details = await services.chats.get_details(message.chat.id)
    who = user_label(details.responsible if details else None)
    extra = "\nТакже добавлен в отвечающие." if added else ""
    await message.reply(f"✅ Ответственный: {who}.{extra}")


@router.message(Command("chat_add_responder", "chat_remove_responder"))
async def cmd_responder(message: Message, command: CommandObject, services: BotServices) -> None:
    target = _reply_target(message)
    if target is None:
        await message.reply(REPLY_REQUIRED)
        return
    if target.is_bot:
        await message.reply(REPLY_TO_BOT)
        return
    add = command.command == "chat_add_responder"
    data = user_data(target)
    if add:
        change = await services.chats.add_responder(message.chat.id, data, _actor(message))
        text = "✅ Добавлен в отвечающие" if change.changed else "Уже в списке отвечающих"
    else:
        change = await services.chats.remove_responder(message.chat.id, data, _actor(message))
        text = "✅ Удалён из отвечающих" if change.changed else "Не был в списке отвечающих"
    await message.reply(f"{text}: {user_label(change.user)}")


@router.errors(ExceptionTypeFilter(ChatNotMonitoredError))
async def on_chat_not_monitored(event: ErrorEvent) -> None:
    message = event.update.message
    if message is not None:
        await message.reply(NOT_MONITORED)
