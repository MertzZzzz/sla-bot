from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.callbacks import (
    PendingReplyAction,
    PendingReplyCallbackData,
    ReassignAction,
    ReassignCallbackData,
)
from app.schemas.users import TelegramUserRead
from app.services.formatting import truncate

NOT_REQUIRED_TEXT = "Ответ не требуется"
REASSIGN_TICKET_TEXT = "👤 Сменить ответственного по сообщению"
REASSIGN_CHAT_TEXT = "👥 Сменить ответственного чата"
BACK_TEXT = "« Назад"
MAX_CANDIDATE_BUTTONS = 30


def _reassign(action: ReassignAction, pending_reply_id: int, user_id: int = 0) -> str:
    return ReassignCallbackData(
        action=action, pending_reply_id=pending_reply_id, user_id=user_id
    ).pack()


def notification_keyboard(pending_reply_id: int) -> InlineKeyboardMarkup:
    """Keyboard attached to every SLA notification."""
    not_required = PendingReplyCallbackData(
        action=PendingReplyAction.NOT_REQUIRED, pending_reply_id=pending_reply_id
    ).pack()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=NOT_REQUIRED_TEXT, callback_data=not_required)],
            [
                InlineKeyboardButton(
                    text=REASSIGN_TICKET_TEXT,
                    callback_data=_reassign(ReassignAction.MENU_TICKET, pending_reply_id),
                )
            ],
            [
                InlineKeyboardButton(
                    text=REASSIGN_CHAT_TEXT,
                    callback_data=_reassign(ReassignAction.MENU_CHAT, pending_reply_id),
                )
            ],
        ]
    )


def candidates_keyboard(
    pending_reply_id: int, action: ReassignAction, candidates: list[TelegramUserRead]
) -> InlineKeyboardMarkup:
    """One button per candidate responder plus "back"."""
    rows = [
        [
            InlineKeyboardButton(
                text=truncate(u.display_name or str(u.telegram_user_id), 40),
                callback_data=_reassign(action, pending_reply_id, u.telegram_user_id),
            )
        ]
        for u in candidates[:MAX_CANDIDATE_BUTTONS]
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=BACK_TEXT, callback_data=_reassign(ReassignAction.BACK, pending_reply_id)
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
