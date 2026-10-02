from __future__ import annotations

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class PendingReplyAction(StrEnum):
    NOT_REQUIRED = "nr"


class PendingReplyCallbackData(CallbackData, prefix="pr"):
    """Packs to ``pr:nr:<id>`` — well below Telegram's 64-byte limit."""

    action: PendingReplyAction
    pending_reply_id: int
