from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.extractors import user_data
from app.bot.keyboards.pending_reply import candidates_keyboard, notification_keyboard
from app.bot.services import BotServices
from app.core.enums import PendingReplyStatus
from app.schemas.callbacks import (
    PendingReplyAction,
    PendingReplyCallbackData,
    ReassignAction,
    ReassignCallbackData,
)
from app.services.formatting import TELEGRAM_MESSAGE_LIMIT
from app.services.notifications import (
    not_required_line,
    reassigned_line,
    replace_responsible_line,
    responsible_label,
)
from app.services.pending_replies import NotRequiredOutcome
from app.services.reassignment import ReassignOutcome, ReassignScope

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
) -> None:
    actor = user_data(callback.from_user)
    result = await services.pending.mark_not_required(
        callback_data.pending_reply_id,
        actor,
        is_admin=await services.admins.is_admin(actor.telegram_user_id),
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
    await _edit(message, f"{message.html_text}\n\n{line}", None)


REASSIGN_DENIED_TEXT: dict[ReassignOutcome, str] = {
    ReassignOutcome.NOT_FOUND: "Ожидание ответа не найдено.",
    ReassignOutcome.FORBIDDEN: "⛔ Недостаточно прав.",
    ReassignOutcome.FINAL: "Сообщение уже закрыто — ответственного не сменить.",
    ReassignOutcome.NO_CANDIDATES: (
        "Некого назначить: добавьте отвечающих в меню (/menu → Чаты → чат → Отвечающие)."
    ),
    ReassignOutcome.INVALID_USER: "Пользователь больше не входит в число отвечающих.",
    ReassignOutcome.UNCHANGED: "Этот пользователь уже ответственный.",
}

MENU_SCOPES: dict[ReassignAction, tuple[ReassignScope, ReassignAction]] = {
    ReassignAction.MENU_TICKET: (ReassignScope.TICKET, ReassignAction.SET_TICKET),
    ReassignAction.MENU_CHAT: (ReassignScope.CHAT, ReassignAction.SET_CHAT),
}
SET_SCOPES: dict[ReassignAction, ReassignScope] = {
    ReassignAction.SET_TICKET: ReassignScope.TICKET,
    ReassignAction.SET_CHAT: ReassignScope.CHAT,
}


@router.callback_query(ReassignCallbackData.filter(F.action.in_(set(MENU_SCOPES))))
async def on_reassign_menu(
    callback: CallbackQuery,
    callback_data: ReassignCallbackData,
    services: BotServices,
) -> None:
    scope, set_action = MENU_SCOPES[callback_data.action]
    actor_id = callback.from_user.id
    options = await services.reassignment.options(
        callback_data.pending_reply_id,
        scope,
        actor_id,
        is_admin=await services.admins.is_admin(actor_id),
    )
    if options.outcome is not ReassignOutcome.OK:
        await callback.answer(REASSIGN_DENIED_TEXT[options.outcome], show_alert=True)
        return
    await callback.answer("Выберите нового ответственного")
    if isinstance(callback.message, Message):
        keyboard = candidates_keyboard(
            callback_data.pending_reply_id, set_action, options.candidates
        )
        await _edit(callback.message, None, keyboard)


@router.callback_query(ReassignCallbackData.filter(F.action == ReassignAction.BACK))
async def on_reassign_back(callback: CallbackQuery, callback_data: ReassignCallbackData) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit(callback.message, None, notification_keyboard(callback_data.pending_reply_id))


@router.callback_query(ReassignCallbackData.filter(F.action.in_(set(SET_SCOPES))))
async def on_reassign_set(
    callback: CallbackQuery,
    callback_data: ReassignCallbackData,
    services: BotServices,
) -> None:
    scope = SET_SCOPES[callback_data.action]
    actor = user_data(callback.from_user)
    result = await services.reassignment.reassign(
        callback_data.pending_reply_id,
        scope,
        callback_data.user_id,
        actor,
        is_admin=await services.admins.is_admin(actor.telegram_user_id),
    )
    if result.outcome is not ReassignOutcome.OK or result.new is None:
        await callback.answer(REASSIGN_DENIED_TEXT[result.outcome], show_alert=True)
        return
    logger.info(
        "responsible reassigned",
        extra={
            "event": f"reassigned_{scope.value}",
            "pending_reply_id": callback_data.pending_reply_id,
            "telegram_user_id": actor.telegram_user_id,
        },
    )
    await callback.answer("Ответственный изменён.")
    if not isinstance(callback.message, Message):
        return
    new_name = result.new.display_name or str(result.new.telegram_user_id)
    text = callback.message.html_text
    if scope is ReassignScope.TICKET:
        text = replace_responsible_line(text, result.new.telegram_user_id, new_name)
    line = reassigned_line(
        chat_scope=scope is ReassignScope.CHAT,
        old=responsible_label(
            result.old.telegram_user_id if result.old else None,
            result.old.display_name if result.old else None,
        ),
        new=responsible_label(result.new.telegram_user_id, new_name),
        actor=responsible_label(actor.telegram_user_id, actor.display_name),
    )
    still_open = result.status in PendingReplyStatus.open_statuses()
    keyboard = notification_keyboard(callback_data.pending_reply_id) if still_open else None
    await _edit(callback.message, f"{text}\n\n{line}", keyboard)


async def _edit(message: Message, text: str | None, keyboard: InlineKeyboardMarkup | None) -> None:
    """Edit text and/or keyboard; failures only affect presentation and are logged."""
    try:
        if text is None:
            await message.edit_reply_markup(reply_markup=keyboard)
        elif len(text) <= TELEGRAM_MESSAGE_LIMIT:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.edit_reply_markup(reply_markup=keyboard)
            await message.reply(text.rsplit("\n\n", 1)[-1])
    except TelegramAPIError as exc:
        _log_edit_failure(message, exc)


def _log_edit_failure(message: Message, exc: TelegramAPIError) -> None:
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
