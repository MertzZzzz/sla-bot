"""Pure presentation helpers (no I/O)."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.enums import ContentType

TELEGRAM_MESSAGE_LIMIT = 4096

_UNITS: tuple[tuple[str, int], ...] = (
    ("дн", 86400),
    ("ч", 3600),
    ("мин", 60),
    ("сек", 1),
)

CONTENT_PLACEHOLDERS: dict[str, str] = {
    ContentType.PHOTO: "[Фото]",
    ContentType.VIDEO: "[Видео]",
    ContentType.ANIMATION: "[GIF]",
    ContentType.DOCUMENT: "[Документ]",
    ContentType.AUDIO: "[Аудио]",
    ContentType.VOICE: "[Голосовое сообщение]",
    ContentType.VIDEO_NOTE: "[Видеосообщение]",
    ContentType.STICKER: "[Стикер]",
    ContentType.CONTACT: "[Контакт]",
    ContentType.LOCATION: "[Геопозиция]",
    ContentType.VENUE: "[Место]",
    ContentType.POLL: "[Опрос]",
    ContentType.OTHER: "[Сообщение]",
    ContentType.TEXT: "[Пустое сообщение]",
}


def format_duration(seconds: float, max_units: int = 2) -> str:
    """Human-readable Russian duration: ``684`` -> ``"11 мин 24 сек"``."""
    total = max(0, round(seconds))
    if total == 0:
        return "0 сек"
    parts: list[str] = []
    for name, size in _UNITS:
        value, total = divmod(total, size)
        if value:
            parts.append(f"{value} {name}")
        elif parts:
            # keep units adjacent: "1 ч 0 мин" is noise, so stop after a gap
            break
        if len(parts) >= max_units:
            break
    return " ".join(parts)


def format_datetime(value: datetime, timezone: str) -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime("%d.%m.%Y %H:%M:%S")


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def escape(text: str) -> str:
    return html.escape(text, quote=False)


def escape_truncated(text: str, limit: int, suffix: str = "…") -> str:
    """HTML-escape ``text`` so that the *escaped* result fits into ``limit`` characters.

    Entities are never cut in half: truncation happens on source characters.
    """
    escaped = escape(text)
    if len(escaped) <= limit:
        return escaped
    budget = max(0, limit - len(suffix))
    parts: list[str] = []
    used = 0
    for char in text:
        piece = escape(char)
        if used + len(piece) > budget:
            break
        parts.append(piece)
        used += len(piece)
    return "".join(parts) + suffix


def content_preview(text: str | None, content_type: str | None, limit: int) -> str:
    """Escaped message text/caption, or a placeholder for non-text content."""
    placeholder = CONTENT_PLACEHOLDERS.get(content_type or ContentType.TEXT, "[Сообщение]")
    if not text or not text.strip():
        return placeholder
    body = escape_truncated(text.strip(), limit)
    if content_type and content_type != ContentType.TEXT:
        return f"{placeholder} {body}"
    return body


def user_mention(telegram_user_id: int, name: str) -> str:
    """HTML mention that works even for users without a username."""
    return f'<a href="tg://user?id={int(telegram_user_id)}">{escape(name)}</a>'


def format_percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def split_message(blocks: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Join blocks with blank lines into as few messages as possible within ``limit``."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        chunk = truncate(block, limit)
        candidate = f"{current}\n\n{chunk}" if current else chunk
        if len(candidate) <= limit:
            current = candidate
            continue
        messages.append(current)
        current = chunk
    if current:
        messages.append(current)
    return messages
