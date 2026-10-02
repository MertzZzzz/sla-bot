"""Inviting pilot participants into a customer chat.

The Bot API cannot add people to a group, so the bot creates a personal one-time invite
link per participant and sends it privately. People who never started the bot cannot
be messaged first; their links go to the admin in the report to forward manually.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from app.bot.services import BotServices
from app.schemas.chats import MonitoredChatDetails
from app.services.formatting import escape, format_datetime, split_message, truncate
from app.services.pilot import PilotView

logger = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=7)
MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}
NO_RIGHTS_TEXT = (
    "Бот не смог создать ссылку-приглашение. Сделайте бота администратором чата "
    "с правом «Приглашать пользователей» (Invite users via link)."
)


class InviteStatus(StrEnum):
    SENT = "sent"  # personal link delivered privately
    MEMBER = "member"  # already in the chat
    MANUAL = "manual"  # link created, admin must forward it


@dataclass(frozen=True, slots=True)
class InviteLine:
    participant: PilotView
    status: InviteStatus
    link: str | None = None
    reason: str | None = None


@dataclass
class InviteReport:
    chat_title: str
    lines: list[InviteLine] = field(default_factory=list)
    error: str | None = None
    expires_at: datetime | None = None

    def count(self, status: InviteStatus) -> int:
        return sum(1 for line in self.lines if line.status is status)


async def _is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    if member.status in MEMBER_STATUSES:
        return True
    return member.status == ChatMemberStatus.RESTRICTED and bool(
        getattr(member, "is_member", False)
    )


async def invite_pilot(
    bot: Bot,
    services: BotServices,
    details: MonitoredChatDetails,
    actor_id: int,
    now: datetime,
) -> InviteReport:
    chat = details.chat
    report = InviteReport(chat_title=chat.title, expires_at=now + INVITE_TTL)
    for person in await services.pilot.resolve_all():
        user_id = person.telegram_user_id
        if user_id is not None and await _is_member(bot, chat.telegram_chat_id, user_id):
            report.lines.append(InviteLine(person, InviteStatus.MEMBER))
            continue
        try:
            invite = await bot.create_chat_invite_link(
                chat.telegram_chat_id,
                name=truncate(f"Пилот {person.label}", 32),
                expire_date=report.expires_at,
                member_limit=1,
            )
        except TelegramAPIError as exc:
            report.error = f"{NO_RIGHTS_TEXT}\n<i>{escape(str(exc))}</i>"
            break
        report.lines.append(
            await _deliver(
                bot,
                person,
                chat_title=chat.title,
                link=invite.invite_link,
                report=report,
                timezone=chat.timezone,
            )
        )
    await services.chats.record_pilot_invite(
        chat.telegram_chat_id,
        actor_id,
        {status.value: report.count(status) for status in InviteStatus} | {"error": report.error},
    )
    logger.info(
        "pilot invites processed",
        extra={"event": "pilot_invited", "telegram_chat_id": chat.telegram_chat_id},
    )
    return report


async def _deliver(
    bot: Bot,
    person: PilotView,
    *,
    chat_title: str,
    link: str,
    report: InviteReport,
    timezone: str,
) -> InviteLine:
    if person.telegram_user_id is None:
        return InviteLine(
            person, InviteStatus.MANUAL, link, "бот не знает этого пользователя (не писал боту)"
        )
    expires = format_datetime(report.expires_at, timezone) if report.expires_at else ""
    text = (
        f"👋 Вас приглашают в чат «{escape(chat_title)}».\n\n"
        f'<a href="{escape(link)}">Вступить в чат</a> — ссылка личная и одноразовая, '
        f"действует до {expires} ({escape(timezone)})."
    )
    try:
        await bot.send_message(person.telegram_user_id, text)
    except TelegramAPIError:
        return InviteLine(person, InviteStatus.MANUAL, link, "не нажимал /start у бота")
    return InviteLine(person, InviteStatus.SENT, link)


def format_invite_report(report: InviteReport) -> list[str]:
    head = [f"📨 <b>Приглашения в «{escape(report.chat_title)}»</b>", ""]
    if report.error:
        head += [f"⚠️ {report.error}", ""]
    if not report.lines and not report.error:
        return ["\n".join([*head, "Список участников пилота пуст."])]
    head += [
        f"Отправлено в личку: {report.count(InviteStatus.SENT)}",
        f"Уже в чате: {report.count(InviteStatus.MEMBER)}",
        f"Переслать вручную: {report.count(InviteStatus.MANUAL)}",
    ]
    blocks = ["\n".join(head)]
    manual = [line for line in report.lines if line.status is InviteStatus.MANUAL]
    if manual:
        rows = ["<b>Перешлите эти ссылки вручную</b> (каждая — для одного человека):"]
        rows += [
            f"• {escape(line.participant.label)} — {escape(line.link or '')} ({line.reason})"
            for line in manual
        ]
        blocks.append("\n".join(rows))
    for status, title in (
        (InviteStatus.SENT, "Получили ссылку"),
        (InviteStatus.MEMBER, "Уже в чате"),
    ):
        names = [escape(line.participant.label) for line in report.lines if line.status is status]
        if names:
            blocks.append(f"<b>{title}:</b> " + ", ".join(names))
    return split_message(blocks)
