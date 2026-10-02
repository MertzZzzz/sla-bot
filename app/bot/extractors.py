"""Conversion of aiogram objects into transport-agnostic DTOs."""

from __future__ import annotations

from aiogram.enums import ChatType as TgChatType
from aiogram.enums import ContentType as TgContentType
from aiogram.enums import MessageEntityType
from aiogram.types import Message, User

from app.core.enums import ContentType
from app.schemas.pending_replies import IncomingMessage
from app.schemas.users import TelegramUserData

# Message kinds that carry user content. Everything else (joins, pins, topic
# creation, etc.) is a service message and never opens a ticket.
USEFUL_CONTENT: dict[str, ContentType] = {
    TgContentType.TEXT: ContentType.TEXT,
    TgContentType.PHOTO: ContentType.PHOTO,
    TgContentType.VIDEO: ContentType.VIDEO,
    TgContentType.ANIMATION: ContentType.ANIMATION,
    TgContentType.DOCUMENT: ContentType.DOCUMENT,
    TgContentType.AUDIO: ContentType.AUDIO,
    TgContentType.VOICE: ContentType.VOICE,
    TgContentType.VIDEO_NOTE: ContentType.VIDEO_NOTE,
    TgContentType.STICKER: ContentType.STICKER,
    TgContentType.CONTACT: ContentType.CONTACT,
    TgContentType.LOCATION: ContentType.LOCATION,
    TgContentType.VENUE: ContentType.VENUE,
    TgContentType.POLL: ContentType.POLL,
}

GROUP_TYPES = frozenset({TgChatType.GROUP, TgChatType.SUPERGROUP})


def user_data(user: User) -> TelegramUserData:
    return TelegramUserData(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_bot=user.is_bot,
    )


def is_bot_command(message: Message) -> bool:
    return any(
        e.type == MessageEntityType.BOT_COMMAND and e.offset == 0 for e in message.entities or ()
    )


def _is_ignored(message: Message) -> bool:
    user = message.from_user
    return (
        message.chat.type not in GROUP_TYPES
        or user is None
        or user.is_bot
        # anonymous admin or linked channel post
        or message.sender_chat is not None
        or message.content_type not in USEFUL_CONTENT
        or is_bot_command(message)
    )


def extract_incoming(message: Message, update_id: int | None = None) -> IncomingMessage | None:
    """Return a DTO for SLA processing, or ``None`` if the message must be ignored."""
    if _is_ignored(message):
        return None
    user = message.from_user
    assert user is not None
    content_type = USEFUL_CONTENT[message.content_type]
    text = message.text if message.text is not None else message.caption
    if content_type is ContentType.TEXT and not (text and text.strip()):
        return None
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
    return IncomingMessage(
        update_id=update_id,
        telegram_chat_id=message.chat.id,
        chat_title=message.chat.title or str(message.chat.id),
        chat_type=message.chat.type,
        chat_username=message.chat.username,
        message_id=message.message_id,
        message_thread_id=message.message_thread_id,
        is_topic_message=bool(message.is_topic_message),
        reply_to_message_id=reply_to,
        date=message.date,
        author=user_data(user),
        text=text,
        content_type=content_type,
    )
