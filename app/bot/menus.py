"""Settings menu screens: pure functions from DTOs to (text, inline keyboard)."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.schemas.callbacks import MenuAction as A
from app.schemas.callbacks import MenuCallbackData
from app.schemas.chats import MonitoredChatDetails, MonitoredChatRead
from app.schemas.pending_replies import PendingReplyRead
from app.schemas.users import TelegramUserRead
from app.services.admins import AdminView
from app.services.formatting import (
    TELEGRAM_MESSAGE_LIMIT,
    escape,
    format_datetime,
    format_duration,
    truncate,
)

CHATS_PAGE_SIZE = 10
PEOPLE_PAGE_SIZE = 8
SLA_PRESETS_MINUTES = (5, 10, 15, 30, 60, 120, 240, 480, 1440)
TIMEZONES = (
    "Europe/Kaliningrad",
    "Europe/Moscow",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Novosibirsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
    "UTC",
)
NOT_SET = "<i>не задано</i>"

MODE_TITLES: dict[ReplyMatchMode, str] = {
    ReplyMatchMode.ANY_RESPONDER_MESSAGE: "Любое сообщение",
    ReplyMatchMode.REPLY_ONLY: "Только Reply",
    ReplyMatchMode.THREAD_OR_REPLY: "Reply или топик",
}
MODE_DESCRIPTIONS: dict[ReplyMatchMode, str] = {
    ReplyMatchMode.ANY_RESPONDER_MESSAGE: (
        "любое сообщение отвечающего закрывает самое старое ожидание"
    ),
    ReplyMatchMode.REPLY_ONLY: "ответом считается только Reply на исходное сообщение",
    ReplyMatchMode.THREAD_OR_REPLY: (
        "Reply на исходное сообщение или любое сообщение в том же топике"
    ),
}
CHAT_TYPE_TITLES: dict[ChatType, str] = {
    ChatType.GROUP: "группа",
    ChatType.SUPERGROUP: "супергруппа",
}


@dataclass(frozen=True, slots=True)
class Screen:
    text: str
    markup: InlineKeyboardMarkup


def cb(action: A, i: int = 0, v: str = "", p: int = 0) -> str:
    return MenuCallbackData(a=action, i=i, v=v, p=p).pack()


def button(text: str, action: A, i: int = 0, v: str = "", p: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb(action, i, v, p))


def keyboard(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def back_to_chat(chat_id: int) -> list[InlineKeyboardButton]:
    return [button("« К настройкам чата", A.CHAT, chat_id)]


def user_name(user: TelegramUserRead) -> str:
    if user.display_name:
        return user.display_name
    if user.username:
        return f"@{user.username}"
    return str(user.telegram_user_id)


def user_html(user: TelegramUserRead | None) -> str:
    if user is None:
        return NOT_SET
    username = f" (@{escape(user.username)})" if user.username else ""
    return f"{escape(user_name(user))}{username}"


def _pager(action: A, i: int, page: int, total: int, size: int) -> list[InlineKeyboardButton]:
    pages = max(1, -(-total // size))
    if pages == 1:
        return []
    row = []
    if page > 0:
        row.append(button("‹", action, i, p=page - 1))
    row.append(button(f"{page + 1}/{pages}", A.NOOP))
    if page < pages - 1:
        row.append(button("›", action, i, p=page + 1))
    return row


def _page[T](items: list[T], page: int, size: int) -> tuple[list[T], int]:
    pages = max(1, -(-len(items) // size))
    page = min(max(page, 0), pages - 1)
    return items[page * size : (page + 1) * size], page


def prompt_screen(text: str, cancel_to: A, i: int = 0) -> Screen:
    return Screen(text, keyboard([button("✖ Отмена", A.CANCEL, i, v=cancel_to.value)]))


# --- main ---------------------------------------------------------------------------


def main_menu() -> Screen:
    return Screen(
        "⚙️ <b>Настройки SLA-бота</b>\n\nВыберите раздел.",
        keyboard([button("👮 Администраторы", A.ADMINS), button("💬 Чаты", A.CHATS)]),
    )


# --- administrators -----------------------------------------------------------------


def admins_screen(admins: list[AdminView]) -> Screen:
    lines = [f"👮 <b>Администраторы</b> ({len(admins)})", ""]
    if admins:
        lines.append("Нажмите на администратора, чтобы открыть его настройки.")
        lines.append("🔒 — задан в .env, из бота не удаляется.")
    else:
        lines.append("Администраторов пока нет.")
    rows = [
        [button(("🔒 " if a.from_env else "") + truncate(a.label, 40), A.ADMIN, a.telegram_user_id)]
        for a in admins
    ]
    return Screen(
        "\n".join(lines),
        keyboard(
            *rows, [button("➕ Добавить администратора", A.ADMIN_ADD)], [button("« Меню", A.HOME)]
        ),
    )


def admin_screen(admin: AdminView, timezone: str) -> Screen:
    added_by = (
        user_html(admin.added_by)
        if admin.added_by
        else (
            f"<code>{admin.added_by_telegram_id}</code>" if admin.added_by_telegram_id else NOT_SET
        )
    )
    lines = [
        "👮 <b>Администратор</b>",
        "",
        f"Пользователь: {user_html(admin.user) if admin.user else '<i>ещё не писал боту</i>'}",
        f"Telegram ID: <code>{admin.telegram_user_id}</code>",
        "Источник: "
        + (
            "файл .env (APP_TELEGRAM__ADMIN_TELEGRAM_IDS)"
            if admin.from_env
            else "добавлен через бота"
        ),
        f"Кто добавил: {added_by if not admin.from_env else '—'}",
        f"Добавлен: {format_datetime(admin.created_at, timezone)}",
        "Уведомления о новых чатах: "
        + ("✅ включены" if admin.notify_new_chats else "⏸ выключены"),
    ]
    toggle = (
        "🔕 Не уведомлять о новых чатах"
        if admin.notify_new_chats
        else "🔔 Уведомлять о новых чатах"
    )
    rows = [[button(toggle, A.ADMIN_NOTIFY, admin.telegram_user_id)]]
    if not admin.from_env:
        rows.append(
            [button("🗑 Удалить из администраторов", A.ADMIN_DELETE, admin.telegram_user_id)]
        )
    rows.append([button("« Администраторы", A.ADMINS)])
    return Screen("\n".join(lines), keyboard(*rows))


def admin_delete_confirm(admin: AdminView) -> Screen:
    return Screen(
        f"Удалить <b>{escape(admin.label)}</b> из администраторов?",
        keyboard(
            [
                button("🗑 Да, удалить", A.ADMIN_DELETE_CONFIRM, admin.telegram_user_id),
                button("Отмена", A.ADMIN, admin.telegram_user_id),
            ]
        ),
    )


# --- chats --------------------------------------------------------------------------


def chats_screen(chats: list[MonitoredChatRead], page: int) -> Screen:
    if not chats:
        return Screen(
            "💬 <b>Чаты</b>\n\nЧатов пока нет. Добавьте бота в группу заказчика и выполните "
            "в ней команду /chat_add — настройки чата появятся здесь.",
            keyboard([button("« Меню", A.HOME)]),
        )
    visible, page = _page(chats, page, CHATS_PAGE_SIZE)
    rows = [
        [button(("⏸ " if not c.is_enabled else "") + truncate(c.title, 50), A.CHAT, c.id)]
        for c in visible
    ]
    text = f"💬 <b>Чаты</b> ({len(chats)})\n\nВыберите чат, чтобы открыть его настройки."
    if any(not c.is_enabled for c in chats):
        text += "\n⏸ — мониторинг выключен."
    return Screen(
        text,
        keyboard(
            *rows, _pager(A.CHATS, 0, page, len(chats), CHATS_PAGE_SIZE), [button("« Меню", A.HOME)]
        ),
    )


def _notification_lines(chat: MonitoredChatRead) -> list[str]:
    target = (
        f"<code>{chat.notification_chat_id}</code>"
        if chat.notification_chat_id
        else f"{NOT_SET} ⚠️ уведомления о нарушениях не отправляются"
    )
    topic = (
        f"<code>{chat.notification_thread_id}</code>"
        if chat.notification_thread_id
        else f"{NOT_SET} (общий чат)"
    )
    return [f"Чат уведомлений: {target}", f"Топик уведомлений: {topic}"]


def chat_text(details: MonitoredChatDetails) -> str:
    chat = details.chat
    responders = (
        ", ".join(escape(user_name(u)) for u in details.responders)
        if details.responders
        else NOT_SET
    )
    return "\n".join(
        [
            f"💬 <b>{escape(chat.title)}</b>",
            "",
            f"ID чата: <code>{chat.telegram_chat_id}</code> ({CHAT_TYPE_TITLES[chat.chat_type]})",
            "Мониторинг: " + ("✅ включён" if chat.is_enabled else "⏸ выключен"),
            f"Приоритет: {chat.priority.label}",
            f"SLA: {format_duration(chat.sla_seconds)}",
            f"Режим ответа: {MODE_TITLES[chat.reply_match_mode]}"
            f" — {MODE_DESCRIPTIONS[chat.reply_match_mode]}",
            f"Часовой пояс: {escape(chat.timezone)}",
            f"Ответственный: {user_html(details.responsible)}",
            f"Отвечающие ({len(details.responders)}): {responders}",
            *_notification_lines(chat),
            f"Открытых ожиданий: {details.open_tickets}",
            f"Подключён: {format_datetime(chat.created_at, chat.timezone)}",
        ]
    )


def chat_screen(details: MonitoredChatDetails, notice: str | None = None) -> Screen:
    chat = details.chat
    i = chat.id
    toggle = (
        button("⏸ Выключить мониторинг", A.ENABLE, i, "0")
        if chat.is_enabled
        else button("▶️ Включить мониторинг", A.ENABLE, i, "1")
    )
    text = chat_text(details)
    if notice:
        text = f"{notice}\n\n{text}"
    return Screen(
        text,
        keyboard(
            [toggle],
            [button("🔺 Приоритет", A.PRIORITY, i), button("⏱ SLA", A.SLA, i)],
            [button("🔀 Режим ответа", A.MODE, i), button("🌍 Часовой пояс", A.TIMEZONE, i)],
            [
                button("👤 Ответственный", A.RESPONSIBLE, i),
                button("👥 Отвечающие", A.RESPONDERS, i),
            ],
            [button("🔔 Уведомления", A.NOTIFICATIONS, i)],
            [button(f"🕒 Открытые ожидания ({details.open_tickets})", A.OPEN_TICKETS, i)],
            [button("« Чаты", A.CHATS)],
        ),
    )


def _mark(selected: bool, text: str) -> str:
    return f"✓ {text}" if selected else text


def priority_screen(chat: MonitoredChatRead) -> Screen:
    row = [
        button(_mark(p is chat.priority, p.label), A.PRIORITY_SET, chat.id, p.value)
        for p in Priority
    ]
    return Screen(
        f"🔺 <b>Приоритет</b> · {escape(chat.title)}\n\nТекущий: {chat.priority.label}\n"
        "P1 — самый высокий. Приоритет показывается в уведомлениях и статистике.",
        keyboard(row, back_to_chat(chat.id)),
    )


def sla_screen(chat: MonitoredChatRead) -> Screen:
    buttons = [
        button(
            _mark(chat.sla_seconds == m * 60, format_duration(m * 60)),
            A.SLA_SET,
            chat.id,
            str(m * 60),
        )
        for m in SLA_PRESETS_MINUTES
    ]
    rows = [buttons[k : k + 3] for k in range(0, len(buttons), 3)]
    return Screen(
        f"⏱ <b>SLA</b> · {escape(chat.title)}\n\nТекущий: {format_duration(chat.sla_seconds)}\n"
        "Сколько времени есть на ответ. Изменение действует для новых сообщений.",
        keyboard(*rows, [button("✏️ Ввести вручную", A.SLA_INPUT, chat.id)], back_to_chat(chat.id)),
    )


def mode_screen(chat: MonitoredChatRead) -> Screen:
    lines = [f"🔀 <b>Режим ответа</b> · {escape(chat.title)}", ""]
    lines += [f"<b>{MODE_TITLES[m]}</b> — {MODE_DESCRIPTIONS[m]}" for m in ReplyMatchMode]
    rows = [
        [button(_mark(m is chat.reply_match_mode, MODE_TITLES[m]), A.MODE_SET, chat.id, m.value)]
        for m in ReplyMatchMode
    ]
    return Screen("\n".join(lines), keyboard(*rows, back_to_chat(chat.id)))


def timezone_screen(chat: MonitoredChatRead) -> Screen:
    buttons = [
        button(_mark(tz == chat.timezone, tz), A.TIMEZONE_SET, chat.id, tz) for tz in TIMEZONES
    ]
    rows = [buttons[k : k + 2] for k in range(0, len(buttons), 2)]
    return Screen(
        f"🌍 <b>Часовой пояс</b> · {escape(chat.title)}\n\nТекущий: {escape(chat.timezone)}\n"
        "Используется для отображения времени в уведомлениях.",
        keyboard(
            *rows, [button("✏️ Другой (ввести)", A.TIMEZONE_INPUT, chat.id)], back_to_chat(chat.id)
        ),
    )


def _no_people_hint(details: MonitoredChatDetails) -> str:
    if details.candidates():
        return ""
    return (
        "\n\nБот ещё не видел участников этого чата: они появятся здесь, когда напишут в чат. "
        "Или укажите пользователя вручную."
    )


def responsible_screen(details: MonitoredChatDetails, page: int) -> Screen:
    chat = details.chat
    current = details.responsible.telegram_user_id if details.responsible else None
    people, page = _page(details.candidates(), page, PEOPLE_PAGE_SIZE)
    rows = [
        [
            button(
                _mark(u.telegram_user_id == current, truncate(user_name(u), 40)),
                A.RESPONSIBLE_SET,
                chat.id,
                str(u.telegram_user_id),
            )
        ]
        for u in people
    ]
    total = len(details.candidates())
    rows.append(_pager(A.RESPONSIBLE, chat.id, page, total, PEOPLE_PAGE_SIZE))
    if current is not None:
        rows.append([button("✖ Снять ответственного", A.RESPONSIBLE_CLEAR, chat.id)])
    rows.append([button("✏️ Указать вручную", A.RESPONSIBLE_INPUT, chat.id)])
    return Screen(
        f"👤 <b>Ответственный</b> · {escape(chat.title)}\n\n"
        f"Текущий: {user_html(details.responsible)}\n"
        "Его упоминают в уведомлениях о нарушении SLA. Выберите из участников чата:"
        + _no_people_hint(details),
        keyboard(*rows, back_to_chat(chat.id)),
    )


def responders_screen(details: MonitoredChatDetails, page: int) -> Screen:
    chat = details.chat
    active = {u.telegram_user_id for u in details.responders}
    people, page = _page(details.candidates(), page, PEOPLE_PAGE_SIZE)
    rows = [
        [
            button(
                ("✅ " if u.telegram_user_id in active else "➕ ") + truncate(user_name(u), 40),
                A.RESPONDER_TOGGLE,
                chat.id,
                str(u.telegram_user_id),
                page,
            )
        ]
        for u in people
    ]
    rows.append(_pager(A.RESPONDERS, chat.id, page, len(details.candidates()), PEOPLE_PAGE_SIZE))
    rows.append([button("✏️ Добавить вручную", A.RESPONDER_INPUT, chat.id)])
    current = (
        ", ".join(escape(user_name(u)) for u in details.responders)
        if details.responders
        else NOT_SET
    )
    return Screen(
        f"👥 <b>Отвечающие</b> · {escape(chat.title)}\n\n"
        f"Сейчас ({len(details.responders)}): {current}\n"
        "Сообщение отвечающего считается ответом; их сообщения не создают ожиданий.\n"
        "✅ — отвечающий (нажмите, чтобы убрать), ➕ — нажмите, чтобы добавить."
        + _no_people_hint(details),
        keyboard(*rows, back_to_chat(chat.id)),
    )


def notifications_screen(chat: MonitoredChatRead) -> Screen:
    rows = [
        [button("📍 Отправлять сюда", A.NOTIFICATIONS_HERE, chat.id)],
        [button("✏️ Указать chat_id вручную", A.NOTIFICATIONS_INPUT, chat.id)],
    ]
    if chat.notification_chat_id:
        rows.append([button("✖ Отключить уведомления", A.NOTIFICATIONS_CLEAR, chat.id)])
    return Screen(
        "\n".join(
            [
                f"🔔 <b>Уведомления</b> · {escape(chat.title)}",
                "",
                *_notification_lines(chat),
                "",
                "«Отправлять сюда» — в текущий чат (и текущий топик, если меню открыто в топике). "
                "Бот должен быть участником чата уведомлений и иметь право писать.",
            ]
        ),
        keyboard(*rows, back_to_chat(chat.id)),
    )


def open_tickets_screen(details: MonitoredChatDetails, tickets: list[PendingReplyRead]) -> Screen:
    chat = details.chat
    lines = [f"🕒 <b>Открытые ожидания</b> · {escape(chat.title)}", ""]
    if not tickets:
        lines.append("Открытых ожиданий нет.")
    for t in tickets:
        mark = "🔴" if t.status == "overdue" else "🕒"
        author = escape(t.source_author_name or str(t.source_author_telegram_id))
        preview = escape(
            truncate((t.source_text or t.source_content_type or "").replace("\n", " "), 60)
        )
        link = f' <a href="{escape(t.source_message_link)}">→</a>' if t.source_message_link else ""
        line = (
            f"{mark} {author}: {preview}{link}\n"
            f"    дедлайн {format_datetime(t.deadline_at, chat.timezone)}"
        )
        if len("\n".join([*lines, line])) > TELEGRAM_MESSAGE_LIMIT - 200:
            lines.append("…")
            break
        lines.append(line)
    return Screen("\n".join(lines), keyboard(back_to_chat(chat.id)))
