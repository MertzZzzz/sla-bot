from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.bot.extractors import user_data
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import PendingReplyStatus
from app.schemas.callbacks import PendingReplyAction, PendingReplyCallbackData
from app.services.formatting import TELEGRAM_MESSAGE_LIMIT
from app.services.notifications import not_required_line
from app.services.pending_replies import NotRequiredOutcome

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

FINAL_STATUS_TEXT: dict[PendingReplyStatus, str] = {
    PendingReplyStatus.ANSWERED: "Ответ уже получен — отметка не нужна.",
    PendingReplyStatus.NOT_REQUIRED: "Уже отмечено: ответ не требуется.",
    PendingReplyStatus.CANCELLED: "Ожидание ответа отменено.",
}


@router.callback_query(PendingReplyCallbackData.filter(F.action == PendingReplyAction.NOT_REQUIRED))
async def on_not_required(
    callback: CallbackQuery,
    callback_data: PendingReplyCallbackData,
    services: BotServices,
    settings: Settings,
) -> None:
    actor = user_data(callback.from_user)
    result = await services.pending.mark_not_required(
        callback_data.pending_reply_id,
        actor,
        is_admin=settings.is_global_admin(actor.telegram_user_id),
    )
    log_ctx = {
        "pending_reply_id": callback_data.pending_reply_id,
        "telegram_user_id": actor.telegram_user_id,
    }
    match result.outcome:
        case NotRequiredOutcome.NOT_FOUND:
            await callback.answer("Ожидание ответа не найдено.", show_alert=True)
        case NotRequiredOutcome.FORBIDDEN:
            logger.info("not_required denied", extra={"event": "not_required_denied", **log_ctx})
            await callback.answer("⛔ Недостаточно прав.", show_alert=True)
        case NotRequiredOutcome.ALREADY_FINAL:
            assert result.ticket is not None
            await callback.answer(FINAL_STATUS_TEXT.get(result.ticket.status, "Уже обработано."))
        case NotRequiredOutcome.MARKED:
            logger.info("ticket marked not required", extra={"event": "not_required", **log_ctx})
            await callback.answer("Отмечено: ответ не требуется.")
            if isinstance(callback.message, Message):
                await _annotate(
                    callback.message, not_required_line(actor.display_name, actor.telegram_user_id)
                )


async def _annotate(message: Message, line: str) -> None:
    """Append the audit line and drop the keyboard; original text is kept intact."""
    original = message.html_text
    text = f"{original}\n\n{line}"
    try:
        if len(text) <= TELEGRAM_MESSAGE_LIMIT:
            await message.edit_text(text, reply_markup=None)
        else:
            await message.edit_reply_markup(reply_markup=None)
            await message.reply(line)
    except TelegramAPIError as exc:
        # The DB state is already committed; a failed edit only affects presentation.
        logger.warning(
            "failed to update notification message",
            extra={
                "event": "notification_edit_failed",
                "telegram_chat_id": message.chat.id,
                "telegram_message_id": message.message_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
