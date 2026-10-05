"""Inline settings menu (private chat with the bot or an admin chat, global admins only).

Main menu → «Администраторы» / «Чаты». Every screen is an edit of the same message;
manual input (SLA, timezone, users, notification chat) uses a short FSM dialog.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, User

from app.bot import menus
from app.bot.chat_link import ensure_chat_link
from app.bot.chat_target import ResolvedTarget, TargetError, resolve_target
from app.bot.extractors import user_data
from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.filters.settings_context import IsSettingsChat
from app.bot.invites import format_invite_report, invite_pilot
from app.bot.menus import Screen
from app.bot.routers.customer_chat import announce_chat
from app.bot.services import BotServices
from app.bot.texts import HELP_TEXT, NOT_ADMIN_PRIVATE, PILOT_WELCOME
from app.bot.user_input import (
    MAX_SHARED_USERS,
    PEOPLE_USAGE,
    PICK_CUSTOMER_CHAT_REQUEST_ID,
    USAGE,
    UserInputError,
    from_read,
    pick_chat_keyboard,
    pick_user_keyboard,
    resolve_people,
    resolve_user,
)
from app.core.config import Settings
from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.core.types import validate_timezone
from app.schemas.callbacks import MenuAction as A
from app.schemas.callbacks import MenuCallbackData
from app.schemas.chats import MonitoredChatDetails, MonitoredChatUpdate
from app.schemas.commands import (
    ChangeTimezoneCommand,
    CommandArgumentError,
    DateInput,
    SetNotificationCommand,
    SlaInput,
)
from app.schemas.users import TelegramUserData
from app.services.admins import AdminChange
from app.services.formatting import escape, format_duration

logger = logging.getLogger(__name__)
router = Router(name="menu")
router.message.filter(IsSettingsChat())


class MenuInput(StatesGroup):
    add_chat = State()
    pilot_date = State()
    pilot = State()
    sla = State()
    timezone = State()
    notification = State()
    responsible = State()
    responder = State()
    admin = State()


USER_STATES = (MenuInput.responsible, MenuInput.responder, MenuInput.admin)
NOT_ADMIN_ALERT = "⛔ Недостаточно прав."


# --- commands -----------------------------------------------------------------------


async def _remember_user(message: Message, services: BotServices) -> bool:
    """Private chats teach the bot who an @username is (needed to DM pilot invites)."""
    if message.from_user is None or message.chat.type != "private":
        return False
    data = user_data(message.from_user)
    await services.users.upsert(data)
    return await services.pilot.link_user(data)


@router.message(CommandStart(), IsGlobalAdmin())
@router.message(Command("menu"), IsGlobalAdmin())
async def cmd_menu(message: Message, state: FSMContext, services: BotServices) -> None:
    await _remember_user(message, services)
    await _drop_input(message, state)
    screen = menus.main_menu()
    await message.answer(screen.text, reply_markup=screen.markup)


@router.message(Command("help"), IsGlobalAdmin())
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("cancel"), IsGlobalAdmin())
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    had_input = await state.get_state() is not None
    await _drop_input(message, state)
    if not had_input:
        await message.answer("Нечего отменять. /menu — открыть меню.")


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start_not_admin(message: Message, services: BotServices) -> None:
    if await _remember_user(message, services):
        await message.answer(PILOT_WELCOME)
        return
    await message.answer(NOT_ADMIN_PRIVATE)


# --- callbacks ----------------------------------------------------------------------


@dataclass
class Ctx:
    callback: CallbackQuery
    message: Message
    data: MenuCallbackData
    services: BotServices
    settings: Settings
    state: FSMContext
    bot: Bot
    toast: str | None = None
    alert: bool = False
    answered: bool = False
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def actor_id(self) -> int:
        return self.callback.from_user.id


Handler = Callable[[Ctx], Awaitable[Screen | None]]
HANDLERS: dict[A, Handler] = {}
INPUT_ACTIONS = {
    A.DATES_INPUT,
    A.ADMIN_ADD,
    A.PILOT_ADD,
    A.SLA_INPUT,
    A.TIMEZONE_INPUT,
    A.RESPONSIBLE_INPUT,
    A.RESPONDER_INPUT,
    A.NOTIFICATIONS_INPUT,
}


def on[H: Handler](*actions: A) -> Callable[[H], H]:
    def register(fn: H) -> H:
        for action in actions:
            HANDLERS[action] = fn
        return fn

    return register


@router.callback_query(MenuCallbackData.filter())
async def on_menu(
    callback: CallbackQuery,
    *,
    callback_data: MenuCallbackData,
    services: BotServices,
    settings: Settings,
    state: FSMContext,
    bot: Bot,
) -> None:
    message = callback.message
    allowed = (
        isinstance(message, Message)
        and settings.is_settings_chat(message.chat.id, message.chat.type)
        and await services.admins.is_admin(callback.from_user.id)
    )
    if not allowed or not isinstance(message, Message):
        await callback.answer(NOT_ADMIN_ALERT, show_alert=True)
        return
    if callback_data.a not in INPUT_ACTIONS and await state.get_state() is not None:
        await _drop_input(message, state, notify=False)
    ctx = Ctx(callback, message, callback_data, services, settings, state, bot)
    handler = HANDLERS.get(callback_data.a)
    screen = await handler(ctx) if handler else None
    if not ctx.answered:
        await callback.answer(ctx.toast, show_alert=ctx.alert)
    if screen is not None:
        await show(message, screen)


async def show(message: Message, screen: Screen) -> None:
    try:
        await message.edit_text(screen.text, reply_markup=screen.markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@on(A.NOOP)
async def noop(_: Ctx) -> None:
    return None


@on(A.HOME)
async def home(_: Ctx) -> Screen:
    return menus.main_menu()


# administrators


@on(A.ADMINS)
async def admins_list(ctx: Ctx) -> Screen:
    return menus.admins_screen(await ctx.services.admins.list_admins())


@on(A.ADMIN)
async def admin_card(ctx: Ctx) -> Screen:
    admin = await ctx.services.admins.get(ctx.data.i)
    if admin is None:
        ctx.toast = "Администратор не найден."
        return await admins_list(ctx)
    return menus.admin_screen(admin, ctx.settings.app.default_timezone)


@on(A.ADMIN_NOTIFY)
async def admin_notify(ctx: Ctx) -> Screen:
    value = await ctx.services.admins.toggle_notify_new_chats(ctx.data.i)
    if value is not None:
        ctx.toast = "Уведомления о новых чатах включены" if value else "Уведомления выключены"
    return await admin_card(ctx)


@on(A.ADMIN_DELETE)
async def admin_delete(ctx: Ctx) -> Screen:
    admin = await ctx.services.admins.get(ctx.data.i)
    if admin is None:
        return await admins_list(ctx)
    return menus.admin_delete_confirm(admin)


ADMIN_CHANGE_TEXT = {
    AdminChange.OK: "Администратор удалён.",
    AdminChange.NOT_FOUND: "Администратор не найден.",
    AdminChange.PROTECTED: "Администратор из .env удаляется только правкой .env.",
    AdminChange.SELF: "Нельзя удалить самого себя.",
}


@on(A.ADMIN_DELETE_CONFIRM)
async def admin_delete_confirm(ctx: Ctx) -> Screen:
    result = await ctx.services.admins.remove(ctx.data.i, ctx.actor_id)
    ctx.toast = ADMIN_CHANGE_TEXT.get(result)
    ctx.alert = result is not AdminChange.OK
    if result is AdminChange.OK:
        logger.info(
            "admin removed",
            extra={"event": "admin_removed", "telegram_user_id": ctx.data.i},
        )
        return await admins_list(ctx)
    return await admin_card(ctx)


@on(A.ADMIN_ADD)
async def admin_add(ctx: Ctx) -> Screen:
    return await _prompt_user(
        ctx, MenuInput.admin, chat_id=0, title="➕ <b>Новый администратор</b>", cancel_to=A.ADMINS
    )


# pilot participants


@on(A.PILOT)
async def pilot_list(ctx: Ctx) -> Screen:
    return menus.pilot_screen(await ctx.services.pilot.list_all(), ctx.data.p)


@on(A.PILOT_ITEM)
async def pilot_item(ctx: Ctx) -> Screen:
    person = await ctx.services.pilot.get(ctx.data.i)
    if person is None:
        ctx.toast = "Участник не найден."
        return await pilot_list(ctx)
    added_by = None
    if person.added_by_telegram_id:
        known = await ctx.services.users.find(str(person.added_by_telegram_id))
        added_by = menus.user_name(known) if known else str(person.added_by_telegram_id)
    return menus.pilot_item_screen(person, added_by, ctx.settings.app.default_timezone)


@on(A.PILOT_DELETE)
async def pilot_delete(ctx: Ctx) -> Screen:
    person = await ctx.services.pilot.get(ctx.data.i)
    if person is None:
        return await pilot_list(ctx)
    return menus.pilot_delete_confirm(person)


@on(A.PILOT_DELETE_CONFIRM)
async def pilot_delete_confirm(ctx: Ctx) -> Screen:
    removed = await ctx.services.pilot.remove(ctx.data.i)
    ctx.toast = "Удалён из участников пилота" if removed else "Участник не найден."
    return await pilot_list(ctx)


@on(A.PILOT_ADD)
async def pilot_add(ctx: Ctx) -> Screen:
    private = ctx.message.chat.type == "private"
    text = f"🧪 <b>Новые участники пилота</b>\n\n{PEOPLE_USAGE}"
    if private:
        text += "\nИли нажмите «👤 Выбрать пользователя» внизу (до 10 человек за раз)."
        await ctx.message.answer(
            "👇 Выбор пользователей", reply_markup=pick_user_keyboard(MAX_SHARED_USERS)
        )
    return await _prompt(ctx, MenuInput.pilot, text, A.PILOT, reply_kb=private)


@on(A.INVITE)
async def invite(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.invite_confirm_screen(details, await ctx.services.pilot.list_all())


@on(A.INVITE_RUN)
async def invite_run(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    # Creating links and messaging people takes a while: answer the button right away.
    await ctx.callback.answer("Отправляю приглашения…")
    ctx.answered = True
    report = await invite_pilot(ctx.bot, ctx.services, details, ctx.actor_id, datetime.now(UTC))
    first, *rest = format_invite_report(report)
    for chunk in rest:
        await ctx.message.answer(chunk)
    return menus.invite_result_screen(details.chat.id, first)


# chats


@on(A.CHATS)
async def chats_list(ctx: Ctx) -> Screen:
    private = ctx.message.chat.type == "private"
    return menus.chats_screen(await ctx.services.chats.list_chats(), ctx.data.p, private=private)


@on(A.CHAT_ADD_PICK)
async def chat_add_pick(ctx: Ctx) -> Screen:
    await ctx.message.answer(
        "👇 Выбор группы заказчика",
        reply_markup=pick_chat_keyboard(PICK_CUSTOMER_CHAT_REQUEST_ID),
    )
    return await _prompt(
        ctx,
        MenuInput.add_chat,
        "➕ <b>Подключить чат заказчика</b>\n\nНажмите «👥 Выбрать группу» внизу экрана и "
        "выберите группу, где уже есть бот. Если это форум — затем выберите тему или весь чат.",
        cancel_to=A.CHATS,
        reply_kb=True,
    )


@on(A.CHAT_ADD_CONFIRM)
async def chat_add_confirm(ctx: Ctx) -> Screen:
    thread_id = ctx.data.p or None
    try:
        target = await resolve_target(ctx.bot, int(ctx.data.v), None)
    except (TargetError, ValueError) as exc:
        ctx.toast, ctx.alert = str(exc)[:190], True
        return await chats_list(ctx)
    screen, _ = await _connect_chat(
        ctx.bot, ctx.services, target, ctx.callback.from_user, thread_id=thread_id
    )
    return screen


async def _connect_chat(
    bot: Bot,
    services: BotServices,
    target: ResolvedTarget,
    actor: User,
    *,
    thread_id: int | None,
) -> tuple[Screen, bool]:
    """Add a picked group/topic as a customer chat and return its card."""
    topic_name = await services.chats.topic_name(target.chat_id, thread_id) if thread_id else None
    created, chat = await services.chats.add_chat(
        target.chat_id,
        target.title,
        ChatType(target.chat_type),
        actor.id,
        username=target.username,
        thread_id=thread_id,
        topic_name=topic_name,
    )
    await announce_chat(bot, services, chat, actor, created=created, send_to_actor=False)
    details = await services.chats.get_card(chat.id)
    assert details is not None
    notice = "✅ Чат подключён к мониторингу." if created else "ℹ️ Этот чат уже подключён."
    return menus.chat_screen(details, notice=notice), created


async def _card(ctx: Ctx) -> MonitoredChatDetails | None:
    details = await ctx.services.chats.get_card(ctx.data.i)
    if details is None:
        ctx.toast = "Чат не найден."
    return details


@on(A.CHAT)
async def chat_card(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    if details.chat.chat_link is None and await ensure_chat_link(
        ctx.bot, ctx.services, details.chat
    ):
        details = await _card(ctx) or details  # picked up a link (e.g. bot became admin)
    return menus.chat_screen(details)


async def _update(ctx: Ctx, changes: MonitoredChatUpdate, toast: str) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    await ctx.services.chats.update(
        details.chat.telegram_chat_id, changes, ctx.actor_id, thread_id=details.chat.thread_id
    )
    ctx.toast = toast
    return await chat_card(ctx)


@on(A.ENABLE)
async def enable(ctx: Ctx) -> Screen:
    enabled = ctx.data.v == "1"
    toast = "Мониторинг включён" if enabled else "Мониторинг выключен, открытые ожидания отменены"
    return await _update(ctx, MonitoredChatUpdate(is_enabled=enabled), toast)


SUBMENUS: dict[A, Callable[[MonitoredChatDetails], Screen]] = {
    A.PRIORITY: lambda d: menus.priority_screen(d.chat),
    A.SLA: lambda d: menus.sla_screen(d.chat),
    A.MODE: lambda d: menus.mode_screen(d.chat),
    A.TIMEZONE: lambda d: menus.timezone_screen(d.chat),
}


@on(A.NOTIFICATIONS)
async def notifications(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.notifications_screen(details.chat, private=ctx.message.chat.type == "private")


@on(A.NOTIFICATIONS_PICK)
async def notifications_pick(ctx: Ctx) -> Screen:
    await ctx.message.answer("👇 Выбор группы", reply_markup=pick_chat_keyboard())
    return await _prompt(
        ctx,
        MenuInput.notification,
        "🔔 <b>Чат уведомлений</b>\n\nНажмите «👥 Выбрать группу» внизу экрана и выберите "
        "группу, где уже есть бот. Или введите chat_id вручную.",
        cancel_to=A.NOTIFICATIONS,
        reply_kb=True,
    )


@on(*SUBMENUS)
async def submenu(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return SUBMENUS[ctx.data.a](details)


@on(A.PRIORITY_SET)
async def priority_set(ctx: Ctx) -> Screen:
    priority = Priority(ctx.data.v)
    return await _update(
        ctx, MonitoredChatUpdate(priority=priority), f"Приоритет: {priority.label}"
    )


@on(A.SLA_SET)
async def sla_set(ctx: Ctx) -> Screen:
    seconds = int(ctx.data.v)
    return await _update(
        ctx, MonitoredChatUpdate(sla_seconds=seconds), f"SLA: {format_duration(seconds)}"
    )


@on(A.MODE_SET)
async def mode_set(ctx: Ctx) -> Screen:
    mode = ReplyMatchMode(ctx.data.v)
    return await _update(
        ctx, MonitoredChatUpdate(reply_match_mode=mode), f"Режим: {menus.MODE_TITLES[mode]}"
    )


@on(A.TIMEZONE_SET)
async def timezone_set(ctx: Ctx) -> Screen:
    timezone = validate_timezone(ctx.data.v)
    return await _update(ctx, MonitoredChatUpdate(timezone=timezone), f"Часовой пояс: {timezone}")


@on(A.NOTIFICATIONS_HERE)
async def notifications_here(ctx: Ctx) -> Screen:
    msg = ctx.message
    thread = msg.message_thread_id if msg.is_topic_message else None
    changes = MonitoredChatUpdate(notification_chat_id=msg.chat.id, notification_thread_id=thread)
    return await _update(ctx, changes, "Уведомления будут приходить в этот чат")


@on(A.NOTIFICATIONS_CLEAR)
async def notifications_clear(ctx: Ctx) -> Screen:
    changes = MonitoredChatUpdate(notification_chat_id=None, notification_thread_id=None)
    return await _update(ctx, changes, "Уведомления отключены")


@on(A.DATES)
async def dates(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.dates_screen(details.chat, menus.today_in(details.chat.timezone))


@on(A.DATES_INPUT)
async def dates_input(ctx: Ctx) -> Screen:
    which = "начала" if ctx.data.v == "start" else "окончания"
    screen = await _prompt(
        ctx,
        MenuInput.pilot_date,
        f"📅 <b>Дата {which} пилота</b>\n\nВведите дату: <code>01.11.2026</code>, "
        "<code>1.11.26</code>, <code>2026-11-01</code>, «сегодня» или «завтра». «-» — очистить.",
        cancel_to=A.DATES,
    )
    await ctx.state.update_data(which=ctx.data.v)
    return screen


@on(A.DATES_CLEAR)
async def dates_clear(ctx: Ctx) -> Screen:
    field = "pilot_start" if ctx.data.v == "start" else "pilot_end"
    await _update(ctx, MonitoredChatUpdate.model_validate({field: None}), "Дата очищена")
    return await dates(ctx)


@on(A.CHAT_DELETE)
async def chat_delete(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.chat_delete_confirm(details)


@on(A.CHAT_DELETE_CONFIRM)
async def chat_delete_confirm(ctx: Ctx) -> Screen:
    title = await ctx.services.chats.delete_chat(ctx.data.i, ctx.actor_id)
    ctx.toast = f"Чат «{title}» удалён" if title else "Чат не найден."
    return await chats_list(ctx)


@on(A.OPEN_TICKETS)
async def open_tickets(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    tickets = await ctx.services.pending.list_open(details.chat.id)
    return menus.open_tickets_screen(details, tickets)


# people


@on(A.RESPONSIBLE)
async def responsible(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.responsible_screen(details, ctx.data.p)


@on(A.RESPONDERS)
async def responders(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    return menus.responders_screen(details, ctx.data.p)


def _candidate(details: MonitoredChatDetails, telegram_id: str) -> TelegramUserData | None:
    for user in details.candidates():
        if str(user.telegram_user_id) == telegram_id:
            return from_read(user)
    return None


@on(A.RESPONSIBLE_SET)
async def responsible_set(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    user = _candidate(details, ctx.data.v)
    if user is None:  # stale button or forged callback
        ctx.toast = "Пользователь не найден среди участников чата."
        return menus.responsible_screen(details, 0)
    await ctx.services.chats.set_responsible(
        details.chat.telegram_chat_id, user, ctx.actor_id, thread_id=details.chat.thread_id
    )
    ctx.toast = f"Ответственный: {user.display_name}"
    return await chat_card(ctx)


@on(A.RESPONSIBLE_CLEAR)
async def responsible_clear(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    await ctx.services.chats.clear_responsible(
        details.chat.telegram_chat_id, ctx.actor_id, thread_id=details.chat.thread_id
    )
    ctx.toast = "Ответственный снят"
    return await chat_card(ctx)


@on(A.RESPONDER_TOGGLE)
async def responder_toggle(ctx: Ctx) -> Screen:
    details = await _card(ctx)
    if details is None:
        return await chats_list(ctx)
    user = _candidate(details, ctx.data.v)
    if user is None:
        ctx.toast = "Пользователь не найден среди участников чата."
        return menus.responders_screen(details, ctx.data.p)
    chat_tg = details.chat.telegram_chat_id
    is_responder = any(u.telegram_user_id == user.telegram_user_id for u in details.responders)
    if is_responder:
        await ctx.services.chats.remove_responder(
            chat_tg, user, ctx.actor_id, thread_id=details.chat.thread_id
        )
        ctx.toast = f"{user.display_name} больше не отвечающий"
    else:
        await ctx.services.chats.add_responder(
            chat_tg, user, ctx.actor_id, thread_id=details.chat.thread_id
        )
        ctx.toast = f"{user.display_name} — отвечающий"
    return await responders(ctx)


# manual input prompts


@on(A.SLA_INPUT)
async def sla_input(ctx: Ctx) -> Screen:
    return await _prompt(
        ctx,
        MenuInput.sla,
        "⏱ <b>SLA</b>\n\nВведите длительность: <code>45</code> (минут), <code>1ч 30м</code>, "
        "<code>2h</code>, <code>90s</code>, <code>1d</code>. Максимум 30 суток.",
        cancel_to=A.SLA,
    )


@on(A.TIMEZONE_INPUT)
async def timezone_input(ctx: Ctx) -> Screen:
    return await _prompt(
        ctx,
        MenuInput.timezone,
        "🌍 <b>Часовой пояс</b>\n\nВведите название IANA, например <code>Europe/Berlin</code> "
        "или <code>Asia/Almaty</code>.",
        cancel_to=A.TIMEZONE,
    )


@on(A.NOTIFICATIONS_INPUT)
async def notifications_input(ctx: Ctx) -> Screen:
    return await _prompt(
        ctx,
        MenuInput.notification,
        "🔔 <b>Чат уведомлений</b>\n\nВведите chat_id и, если нужно, ID топика через пробел: "
        "<code>-1001234567890 42</code>.\nID топика — число из ссылки на сообщение в топике "
        "<code>t.me/c/…/&lt;topic&gt;/&lt;message&gt;</code>.",
        cancel_to=A.NOTIFICATIONS,
    )


@on(A.RESPONSIBLE_INPUT)
async def responsible_input(ctx: Ctx) -> Screen:
    return await _prompt_user(
        ctx, MenuInput.responsible, ctx.data.i, "👤 <b>Ответственный</b>", A.RESPONSIBLE
    )


@on(A.RESPONDER_INPUT)
async def responder_input(ctx: Ctx) -> Screen:
    return await _prompt_user(
        ctx, MenuInput.responder, ctx.data.i, "👥 <b>Новый отвечающий</b>", A.RESPONDERS
    )


@on(A.CANCEL)
async def cancel(ctx: Ctx) -> Screen:
    # The input state was already dropped in on_menu; show the screen we came from.
    target = A(ctx.data.v) if ctx.data.v in A._value2member_map_ else A.HOME
    ctx.data = MenuCallbackData(a=target, i=ctx.data.i)
    handler = HANDLERS.get(target, home)
    return await handler(ctx) or menus.main_menu()


async def _prompt(
    ctx: Ctx, state: State, text: str, cancel_to: A, *, reply_kb: bool = False
) -> Screen:
    await ctx.state.set_state(state)
    await ctx.state.set_data(
        {
            "chat_id": ctx.data.i,
            "prompt_message_id": ctx.message.message_id,
            "reply_kb": reply_kb,
        }
    )
    return menus.prompt_screen(text + "\n\n/cancel — отменить ввод.", cancel_to, ctx.data.i)


async def _prompt_user(ctx: Ctx, state: State, chat_id: int, title: str, cancel_to: A) -> Screen:
    private = ctx.message.chat.type == "private"
    text = f"{title}\n\n{USAGE}"
    if private:
        text += "\nИли нажмите кнопку «👤 Выбрать пользователя» внизу экрана."
        await ctx.message.answer("👇 Выбор пользователя", reply_markup=pick_user_keyboard())
    return await _prompt(ctx, state, text, cancel_to, reply_kb=private)


# --- manual input handlers ------------------------------------------------------------

NOT_COMMAND = ~F.text.startswith("/")


async def _input_chat(
    message: Message, state: FSMContext, services: BotServices
) -> MonitoredChatDetails | None:
    data = await state.get_data()
    details = await services.chats.get_card(int(data.get("chat_id", 0)))
    if details is None:
        await _drop_input(message, state)
        await message.answer("Чат не найден. /menu — открыть меню.")
    return details


async def _finish(message: Message, state: FSMContext, bot: Bot, screen: Screen) -> None:
    data = await state.get_data()
    await state.clear()
    prompt_id = data.get("prompt_message_id")
    if prompt_id:
        with contextlib.suppress(TelegramAPIError):
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id, message_id=int(prompt_id), reply_markup=None
            )
    if data.get("reply_kb"):
        await message.answer("✅ Готово", reply_markup=ReplyKeyboardRemove())
    await message.answer(screen.text, reply_markup=screen.markup)


async def _drop_input(message: Message, state: FSMContext, *, notify: bool = True) -> None:
    data = await state.get_data()
    if await state.get_state() is None:
        return
    await state.clear()
    if data.get("reply_kb"):
        await message.answer("Ввод отменён.", reply_markup=ReplyKeyboardRemove())
    elif notify:
        await message.answer("Ввод отменён.")


async def _chat_updated(
    message: Message,
    *,
    state: FSMContext,
    services: BotServices,
    bot: Bot,
    changes: MonitoredChatUpdate,
    notice: str,
) -> None:
    details = await _input_chat(message, state, services)
    if details is None or message.from_user is None:
        return
    await services.chats.update(
        details.chat.telegram_chat_id,
        changes,
        message.from_user.id,
        thread_id=details.chat.thread_id,
    )
    fresh = await services.chats.get_card(details.chat.id)
    assert fresh is not None
    await _finish(message, state, bot, menus.chat_screen(fresh, notice=notice))


@router.message(StateFilter(MenuInput.sla), IsGlobalAdmin(), NOT_COMMAND)
async def input_sla(message: Message, state: FSMContext, services: BotServices, bot: Bot) -> None:
    try:
        value = SlaInput.parse(message.text)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    notice = f"✅ SLA: {format_duration(value.sla_seconds)}"
    await _chat_updated(
        message,
        state=state,
        services=services,
        bot=bot,
        changes=MonitoredChatUpdate(sla_seconds=value.sla_seconds),
        notice=notice,
    )


@router.message(StateFilter(MenuInput.timezone), IsGlobalAdmin(), NOT_COMMAND)
async def input_timezone(
    message: Message, state: FSMContext, services: BotServices, bot: Bot
) -> None:
    try:
        value = ChangeTimezoneCommand.parse(message.text)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    notice = f"✅ Часовой пояс: {escape(value.timezone)}"
    await _chat_updated(
        message,
        state=state,
        services=services,
        bot=bot,
        changes=MonitoredChatUpdate(timezone=value.timezone),
        notice=notice,
    )


@router.message(StateFilter(MenuInput.notification), IsGlobalAdmin(), NOT_COMMAND)
async def input_notification(
    message: Message, state: FSMContext, services: BotServices, bot: Bot
) -> None:
    try:
        if message.chat_shared is not None:  # Telegram's native chat picker
            value = SetNotificationCommand(notification_chat_id=message.chat_shared.chat_id)
        else:
            value = SetNotificationCommand.parse(message.text)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    try:
        target = await resolve_target(bot, value.notification_chat_id, value.notification_thread_id)
    except TargetError as exc:
        await message.reply(str(exc))
        return
    changes = MonitoredChatUpdate(
        notification_chat_id=target.chat_id,
        notification_thread_id=value.notification_thread_id,
    )
    notice = f"✅ Чат уведомлений: «{escape(target.title)}» (<code>{target.chat_id}</code>)"
    if target.chat_id != value.notification_chat_id:
        notice += (
            f"\nℹ️ Вы ввели <code>{value.notification_chat_id}</code>: в Bot API ID групп "
            "отрицательные, бот подобрал правильный."
        )
    await _chat_updated(
        message, state=state, services=services, bot=bot, changes=changes, notice=notice
    )


@router.message(StateFilter(*USER_STATES), IsGlobalAdmin(), NOT_COMMAND)
async def input_user(message: Message, state: FSMContext, services: BotServices, bot: Bot) -> None:
    current = await state.get_state()
    if current == MenuInput.admin.state:
        await _input_admin(message, state, services, bot)
        return
    details = await _input_chat(message, state, services)
    if details is None or message.from_user is None:
        return
    try:
        user = await resolve_user(message, bot, services, details.chat.telegram_chat_id)
    except UserInputError as exc:
        await message.reply(str(exc))
        return
    chat_tg = details.chat.telegram_chat_id
    if current == MenuInput.responsible.state:
        await services.chats.set_responsible(
            chat_tg, user, message.from_user.id, thread_id=details.chat.thread_id
        )
        notice = f"✅ Ответственный: {escape(user.display_name)}"
    else:
        change = await services.chats.add_responder(
            chat_tg, user, message.from_user.id, thread_id=details.chat.thread_id
        )
        verb = "добавлен в отвечающие" if change.changed else "уже среди отвечающих"
        notice = f"✅ {escape(user.display_name)} {verb}"
    fresh = await services.chats.get_card(details.chat.id)
    assert fresh is not None
    await _finish(message, state, bot, menus.chat_screen(fresh, notice=notice))


async def _input_admin(
    message: Message, state: FSMContext, services: BotServices, bot: Bot
) -> None:
    assert message.from_user is not None
    try:
        user = await resolve_user(message, bot, services, None)
    except UserInputError as exc:
        await message.reply(str(exc))
        return
    result = await services.admins.add(user, message.from_user.id)
    notice = (
        f"✅ {escape(user.display_name)} теперь администратор."
        if result is AdminChange.OK
        else f"ℹ️ {escape(user.display_name)} уже администратор."
    )
    logger.info(
        "admin added", extra={"event": "admin_added", "telegram_user_id": user.telegram_user_id}
    )
    screen = menus.admins_screen(await services.admins.list_admins())
    await _finish(message, state, bot, Screen(f"{notice}\n\n{screen.text}", screen.markup))


@router.message(StateFilter(MenuInput.pilot), IsGlobalAdmin(), NOT_COMMAND)
async def input_pilot(message: Message, state: FSMContext, services: BotServices, bot: Bot) -> None:
    assert message.from_user is not None
    try:
        people = await resolve_people(message, bot, services)
    except UserInputError as exc:
        await message.reply(str(exc))
        return
    result = await services.pilot.add(people, message.from_user.id)
    notice_lines = []
    if result.added:
        notice_lines.append("✅ Добавлены: " + ", ".join(escape(x) for x in result.added))
    if result.already:
        notice_lines.append("ℹ️ Уже в списке: " + ", ".join(escape(x) for x in result.already))
    screen = menus.pilot_screen(
        await services.pilot.list_all(), 0, notice="\n".join(notice_lines) or None
    )
    await _finish(message, state, bot, screen)


@router.message(StateFilter(MenuInput.add_chat), IsGlobalAdmin(), NOT_COMMAND)
async def input_add_chat(
    message: Message, state: FSMContext, services: BotServices, settings: Settings, bot: Bot
) -> None:
    shared = message.chat_shared
    if shared is None or message.from_user is None:
        await message.reply("Нажмите кнопку «👥 Выбрать группу» внизу экрана или /cancel.")
        return
    try:
        target = await resolve_target(bot, shared.chat_id, None)
    except TargetError as exc:
        await message.reply(str(exc))
        return
    if target.chat_type not in ("group", "supergroup") or settings.is_admin_chat(target.chat_id):
        await message.reply("Это не группа заказчика — выберите другую группу.")
        return
    if target.is_forum:
        topics = await services.chats.topics(target.chat_id)
        monitored = {
            c.thread_id
            for c in await services.chats.list_chats()
            if c.telegram_chat_id == target.chat_id
        }
        await _finish(
            message,
            state,
            bot,
            menus.add_chat_topics_screen(target.chat_id, target.title, topics, monitored),
        )
        return
    screen, _ = await _connect_chat(bot, services, target, message.from_user, thread_id=None)
    await _finish(message, state, bot, screen)


@router.message(StateFilter(MenuInput.pilot_date), IsGlobalAdmin(), NOT_COMMAND)
async def input_pilot_date(
    message: Message, state: FSMContext, services: BotServices, bot: Bot
) -> None:
    details = await _input_chat(message, state, services)
    if details is None or message.from_user is None:
        return
    chat = details.chat
    field = "pilot_start" if (await state.get_data()).get("which") == "start" else "pilot_end"
    try:
        value = DateInput.parse(message.text, menus.today_in(chat.timezone)).value
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    start = value if field == "pilot_start" else chat.pilot_start
    end = value if field == "pilot_end" else chat.pilot_end
    if start and end and end < start:
        await message.reply("Окончание пилота не может быть раньше начала — введите другую дату.")
        return
    await services.chats.update(
        chat.telegram_chat_id,
        MonitoredChatUpdate.model_validate({field: value}),
        message.from_user.id,
        thread_id=chat.thread_id,
    )
    fresh = await services.chats.get_card(chat.id)
    assert fresh is not None
    label = "Начало" if field == "pilot_start" else "Окончание"
    shown = value.strftime("%d.%m.%Y") if value else "очищено"
    screen = menus.dates_screen(fresh.chat, menus.today_in(chat.timezone))
    await _finish(
        message, state, bot, Screen(f"✅ {label} пилота: {shown}\n\n{screen.text}", screen.markup)
    )
