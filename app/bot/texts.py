from __future__ import annotations

from app.core.enums import ReplyMatchMode
from app.schemas.chats import MonitoredChatDetails
from app.schemas.pending_replies import PendingReplyRead
from app.schemas.users import TelegramUserRead
from app.services.formatting import (
    escape,
    format_datetime,
    format_duration,
    split_message,
    truncate,
)

HELP_TEXT = """<b>SLA-бот: контроль времени ответа в рабочих чатах</b>

Команды настройки выполняются <b>в самом отслеживаемом чате</b>
и доступны только глобальным администраторам.

<b>Общие</b>
/start — краткая справка
/help — список команд
/stats [days] — статистика за период (1–365, по умолчанию 30); работает и в личке

<b>Настройка текущего чата</b>
/chat_add — добавить чат в мониторинг
/chat_enable, /chat_disable — включить/выключить мониторинг (история сохраняется)
/chat_settings — текущие настройки
/chat_sla &lt;seconds&gt; — SLA в секундах
/chat_priority &lt;p1|p2|p3|p4&gt; — приоритет
/chat_mode &lt;any_responder_message|reply_only|thread_or_reply&gt; — режим сопоставления ответа
/chat_timezone &lt;IANA&gt; — часовой пояс отображения
/chat_responsible — (Reply на сообщение) назначить ответственного
/chat_add_responder — (Reply) добавить отвечающего
/chat_remove_responder — (Reply) удалить отвечающего
/chat_responders — список отвечающих
/chat_notification &lt;chat_id&gt; [thread_id] — куда слать уведомления о нарушении SLA
/pending — открытые ожидания ответа в этом чате"""

START_TEXT = (
    "Привет! Я слежу, чтобы сообщения в рабочих чатах не оставались без ответа дольше SLA.\n\n"
    "Добавьте меня в группу, отключите privacy mode в @BotFather и выполните /chat_add в группе.\n"
    "/help — список команд."
)

NOT_ADMIN = "⛔ Недостаточно прав: команда доступна только глобальным администраторам."
GROUP_ONLY = "Эта команда выполняется в группе, которую нужно отслеживать."
NOT_MONITORED = "Этот чат не отслеживается. Выполните /chat_add."
REPLY_REQUIRED = "Ответьте этой командой (Reply) на сообщение нужного пользователя."
REPLY_TO_BOT = "Нельзя назначить бота."

MODE_DESCRIPTIONS: dict[ReplyMatchMode, str] = {
    ReplyMatchMode.ANY_RESPONDER_MESSAGE: (
        "любое сообщение отвечающего закрывает самое старое ожидание"
    ),
    ReplyMatchMode.REPLY_ONLY: "только Reply на исходное сообщение",
    ReplyMatchMode.THREAD_OR_REPLY: "Reply на исходное сообщение или сообщение в том же топике",
}


def user_label(user: TelegramUserRead | None) -> str:
    if user is None:
        return "не назначен"
    name = user.display_name or str(user.telegram_user_id)
    suffix = f" (@{user.username})" if user.username else ""
    return f"{escape(name)}{escape(suffix)} [<code>{user.telegram_user_id}</code>]"


def chat_settings_text(details: MonitoredChatDetails) -> str:
    chat = details.chat
    notification = (
        f"<code>{chat.notification_chat_id}</code>"
        + (
            f", topic <code>{chat.notification_thread_id}</code>"
            if chat.notification_thread_id
            else ""
        )
        if chat.notification_chat_id
        else "⚠️ не задан (/chat_notification)"
    )
    return "\n".join(
        [
            f"<b>Настройки чата «{escape(chat.title)}»</b>",
            f"ID: <code>{chat.telegram_chat_id}</code>",
            f"Мониторинг: {'✅ включён' if chat.is_enabled else '⏸ выключен'}",
            f"Приоритет: {chat.priority.label}",
            f"SLA: {format_duration(chat.sla_seconds)} ({chat.sla_seconds} сек)",
            f"Режим: <code>{chat.reply_match_mode.value}</code>"
            f" — {MODE_DESCRIPTIONS[chat.reply_match_mode]}",
            f"Часовой пояс: {escape(chat.timezone)}",
            f"Ответственный: {user_label(details.responsible)}",
            f"Отвечающих: {len(details.responders)}",
            f"Уведомления: {notification}",
        ]
    )


def responders_text(details: MonitoredChatDetails) -> str:
    if not details.responders:
        return "Отвечающие не назначены. Используйте /chat_add_responder (Reply на сообщение)."
    lines = [f"{i}. {user_label(u)}" for i, u in enumerate(details.responders, start=1)]
    return "<b>Отвечающие:</b>\n" + "\n".join(lines)


def pending_text(tickets: list[PendingReplyRead], timezone: str) -> list[str]:
    if not tickets:
        return ["✅ Открытых ожиданий ответа нет."]
    lines = [f"<b>Открытые ожидания ({len(tickets)}):</b>"]
    for t in tickets:
        mark = "🔴" if t.status == "overdue" else "🕒"
        author = escape(t.source_author_name or str(t.source_author_telegram_id))
        preview = escape(
            truncate((t.source_text or t.source_content_type or "").replace("\n", " "), 60)
        )
        link = f' <a href="{escape(t.source_message_link)}">→</a>' if t.source_message_link else ""
        lines.append(
            f"{mark} #{t.id} {author}: {preview}{link}\n"
            f"    получено {format_datetime(t.created_at, timezone)}, "
            f"дедлайн {format_datetime(t.deadline_at, timezone)}"
        )
    return split_message(lines)
