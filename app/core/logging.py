"""Structured logging on top of the standard library.

Pass context through ``extra={...}``; known context keys are emitted as top-level
JSON fields. Secrets must never be put into log records.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import LoggingSettings

CONTEXT_KEYS: tuple[str, ...] = (
    "event",
    "update_id",
    "telegram_chat_id",
    "telegram_message_id",
    "pending_reply_id",
    "outbox_event_id",
    "telegram_user_id",
    "celery_task_id",
    "error_type",
    "error_message",
    "count",
    "attempt",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ContextTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = " ".join(
            f"{key}={getattr(record, key)}"
            for key in CONTEXT_KEYS
            if getattr(record, key, None) is not None
        )
        return f"{base} {ctx}" if ctx else base


def configure_logging(settings: LoggingSettings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ContextTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.level)
    # aiogram logs full update payloads at DEBUG; keep third-party noise down.
    for noisy in ("aiogram.event", "httpx", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))
