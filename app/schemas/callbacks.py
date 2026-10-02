from __future__ import annotations

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class PendingReplyAction(StrEnum):
    NOT_REQUIRED = "nr"


class PendingReplyCallbackData(CallbackData, prefix="pr"):
    """Packs to ``pr:nr:<id>`` — well below Telegram's 64-byte limit."""

    action: PendingReplyAction
    pending_reply_id: int


class ReassignAction(StrEnum):
    MENU_TICKET = "mt"  # show candidates for this message's responsible
    MENU_CHAT = "mc"  # show candidates for the chat's responsible
    SET_TICKET = "st"
    SET_CHAT = "sc"
    BACK = "bk"


class ReassignCallbackData(CallbackData, prefix="ra"):
    """Packs to ``ra:st:<ticket_id>:<telegram_user_id>`` — under 64 bytes for any IDs."""

    action: ReassignAction
    pending_reply_id: int
    user_id: int = 0
