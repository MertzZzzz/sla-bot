from __future__ import annotations

from typing import Any

from aiogram.types import Chat, MessageEntity, PhotoSize, User
from aiogram.types import Message as TgMessage

from app.bot.extractors import extract_incoming
from app.core.enums import ContentType
from tests.factories import CHAT_ID, T0

HUMAN = User(id=3000, is_bot=False, first_name="Анна", last_name="Петрова", username="anna")
BOT = User(id=9, is_bot=True, first_name="Bot")
GROUP = Chat(id=CHAT_ID, type="supergroup", title="Поддержка VIP")


def msg(**kwargs: Any) -> TgMessage:
    data: dict[str, Any] = {"message_id": 10, "date": T0, "chat": GROUP, "from_user": HUMAN}
    data.update(kwargs)
    return TgMessage(**data)


def test_plain_text_message() -> None:
    result = extract_incoming(msg(text="Вопрос"), update_id=77)
    assert result is not None
    assert result.update_id == 77
    assert result.telegram_chat_id == CHAT_ID
    assert result.author.telegram_user_id == 3000
    assert result.author.display_name == "Анна Петрова"
    assert result.text == "Вопрос"
    assert result.content_type is ContentType.TEXT


def test_media_with_caption_is_useful() -> None:
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    result = extract_incoming(msg(photo=photo, caption="Скрин ошибки"))
    assert result is not None
    assert result.content_type is ContentType.PHOTO
    assert result.text == "Скрин ошибки"


def test_media_without_caption_is_useful() -> None:
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    result = extract_incoming(msg(photo=photo))
    assert result is not None and result.text is None


def test_ignored_messages() -> None:
    assert extract_incoming(msg(text="hi", from_user=BOT)) is None
    assert extract_incoming(msg(text="hi", from_user=None)) is None
    assert extract_incoming(msg(new_chat_members=[HUMAN])) is None
    assert extract_incoming(msg(text="   ")) is None
    private = Chat(id=3000, type="private")
    assert extract_incoming(msg(text="hi", chat=private)) is None
    anon = Chat(id=CHAT_ID, type="supergroup", title="x")
    assert extract_incoming(msg(text="hi", sender_chat=anon)) is None


def test_bot_commands_are_ignored() -> None:
    entity = MessageEntity(type="bot_command", offset=0, length=8)
    assert extract_incoming(msg(text="/pending", entities=[entity])) is None


def test_reply_and_topic_fields() -> None:
    parent = msg(message_id=5, text="root")
    result = extract_incoming(
        msg(text="ответ", reply_to_message=parent, message_thread_id=5, is_topic_message=True)
    )
    assert result is not None
    assert result.reply_to_message_id == 5
    assert result.message_thread_id == 5
    assert result.is_topic_message
