"""Validate a notification chat ID entered by hand.

Bot API chat IDs of groups are negative (``-123…`` for basic groups, ``-100…`` for
supergroups and channels); positive IDs are private chats with users. People often copy
the number without the prefix, so a positive number is also tried as ``-N`` and
``-100N``. The first chat the bot can actually see and write to wins.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError

WRITABLE_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


class TargetError(ValueError):
    """User-facing reason why the chat cannot receive notifications."""


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    chat_id: int
    title: str
    is_forum: bool
    chat_type: str = "supergroup"
    username: str | None = None


def candidates(raw_id: int) -> list[int]:
    if raw_id < 0:
        return [raw_id]
    return [-raw_id, int(f"-100{raw_id}"), raw_id]


async def resolve_target(bot: Bot, raw_id: int, thread_id: int | None) -> ResolvedTarget:
    me = await bot.me()
    last_problem: str | None = None
    for chat_id in candidates(raw_id):
        try:
            chat = await bot.get_chat(chat_id)
        except TelegramAPIError:
            continue
        if chat.type != ChatType.PRIVATE:
            problem = await _write_problem(bot, chat.id, me.id)
            if problem:
                last_problem = problem
                continue
        if thread_id is not None and not chat.is_forum:
            msg = "В этом чате нет топиков — уберите ID топика или выберите форум."
            raise TargetError(msg)
        title = chat.title or chat.full_name or str(chat.id)
        return ResolvedTarget(
            chat_id=chat.id,
            title=title,
            is_forum=bool(chat.is_forum),
            chat_type=str(chat.type),
            username=chat.username,
        )
    msg = last_problem or (
        f"Бот не видит чат <code>{raw_id}</code>. Проверьте, что бот добавлен в этот чат. "
        "ID групп в Telegram отрицательные: <code>-123…</code> или <code>-100…</code>. "
        "Надёжнее открыть /menu прямо в нужной группе (она должна быть в "
        "APP_TELEGRAM__ADMIN_CHAT_IDS) и нажать «📍 Отправлять сюда»."
    )
    raise TargetError(msg)


async def _write_problem(bot: Bot, chat_id: int, bot_id: int) -> str | None:
    try:
        member = await bot.get_chat_member(chat_id, bot_id)
    except TelegramAPIError:
        return "Бот не состоит в этом чате — добавьте его туда."
    if member.status in WRITABLE_STATUSES:
        return None
    if member.status == ChatMemberStatus.RESTRICTED and getattr(member, "can_send_messages", False):
        return None
    return "У бота нет права писать в этот чат."
