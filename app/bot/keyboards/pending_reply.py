from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.callbacks import PendingReplyAction, PendingReplyCallbackData

NOT_REQUIRED_TEXT = "Ответ не требуется"


def not_required_keyboard(pending_reply_id: int) -> InlineKeyboardMarkup:
    data = PendingReplyCallbackData(
        action=PendingReplyAction.NOT_REQUIRED, pending_reply_id=pending_reply_id
    ).pack()
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=NOT_REQUIRED_TEXT, callback_data=data)]]
    )
