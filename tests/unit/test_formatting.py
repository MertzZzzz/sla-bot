from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.formatting import (
    content_preview,
    escape_truncated,
    format_datetime,
    format_duration,
    format_percent,
    split_message,
    user_mention,
)
from app.services.message_links import MessageLinkService


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 сек"),
        (-5, "0 сек"),
        (45, "45 сек"),
        (684, "11 мин 24 сек"),
        (900, "15 мин"),
        (3600, "1 ч"),
        (3660, "1 ч 1 мин"),
        (3605, "1 ч"),
        (90061, "1 дн 1 ч"),
        (59.6, "1 мин"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_datetime_uses_chat_timezone() -> None:
    value = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    assert format_datetime(value, "Europe/Moscow") == "15.01.2026 12:00:00"


def test_escape_truncated_never_breaks_entities() -> None:
    text = "&" * 100
    result = escape_truncated(text, 23)
    assert len(result) <= 23
    assert result.endswith("…")
    assert result[:-1] == "&amp;" * 4


def test_content_preview_escapes_html() -> None:
    assert content_preview("<b>hi</b> & bye", "text", 100) == "&lt;b&gt;hi&lt;/b&gt; &amp; bye"


@pytest.mark.parametrize(
    ("content_type", "text", "expected"),
    [
        ("photo", None, "[Фото]"),
        ("document", "", "[Документ]"),
        ("voice", None, "[Голосовое сообщение]"),
        ("photo", "подпись", "[Фото] подпись"),
        ("text", "   ", "[Пустое сообщение]"),
        ("weird", None, "[Сообщение]"),
    ],
)
def test_content_preview_placeholders(content_type: str, text: str | None, expected: str) -> None:
    assert content_preview(text, content_type, 100) == expected


def test_user_mention_escapes_name() -> None:
    assert user_mention(42, "<Bob>") == '<a href="tg://user?id=42">&lt;Bob&gt;</a>'


def test_format_percent() -> None:
    assert format_percent(None) == "—"
    assert format_percent(0.9286) == "92.9%"


def test_split_message_respects_limit() -> None:
    blocks = ["x" * 1500 for _ in range(5)]
    messages = split_message(blocks, limit=4096)
    assert all(len(m) <= 4096 for m in messages)
    assert sum(m.count("x") for m in messages) == 7500
    assert len(messages) == 3


@pytest.mark.parametrize(
    ("chat_id", "message_id", "thread_id", "username", "expected"),
    [
        (-1001234567890, 5, None, None, "https://t.me/c/1234567890/5"),
        (-1001234567890, 5, 3, None, "https://t.me/c/1234567890/3/5"),
        (-1001234567890, 5, None, "public_chat", "https://t.me/public_chat/5"),
        (-12345, 5, None, None, None),  # basic groups have no links
        (-1001234567890, 0, None, None, None),
        (-1001, 5, None, "bad/name", None),
    ],
)
def test_message_links(
    chat_id: int, message_id: int, thread_id: int | None, username: str | None, expected: str | None
) -> None:
    link = MessageLinkService().build(
        chat_id, message_id, thread_id=thread_id, chat_username=username
    )
    assert link == expected
