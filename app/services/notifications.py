from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.core.config import CelerySettings
from app.core.enums import OutboxStatus, PendingReplyStatus
from app.db.models import OutboxEvent
from app.db.uow import SyncUnitOfWork
from app.schemas.notifications import NotificationPayload, RenderedNotification
from app.services.clock import Clock
from app.services.formatting import (
    TELEGRAM_MESSAGE_LIMIT,
    content_preview,
    escape,
    escape_truncated,
    format_datetime,
    format_duration,
    user_mention,
)
from app.services.telegram_sender import (
    NotificationSender,
    PermanentDeliveryError,
    TransientDeliveryError,
)

logger = logging.getLogger(__name__)

SOURCE_PREVIEW_LIMIT = 3000
NOT_REQUIRED_LINE = "✅ Ответ не требуется. Отметил: {user}"


class DeliveryStatus(StrEnum):
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: DeliveryStatus
    retry_in_seconds: float | None = None
    message_id: int | None = None
    reason: str | None = None


class NotificationConfigError(Exception):
    pass


def render_notification(payload: NotificationPayload, now: datetime) -> RenderedNotification:
    if payload.target_chat_id is None:
        msg = "notification chat is not configured for the monitored chat"
        raise NotificationConfigError(msg)
    responsible = (
        user_mention(payload.responsible_telegram_id, payload.responsible_name or "ответственный")
        if payload.responsible_telegram_id
        else "не назначен"
    )
    merged = payload.message_count > 1
    count_line = []
    if merged:
        last = payload.last_message_date or payload.source_message_date
        count_line = [
            f"Сообщений: {payload.message_count} (последнее "
            f"{format_datetime(last, payload.timezone)})"
        ]
    head = "\n".join(
        [
            "🔴 <b>SLA нарушен</b>",
            "",
            f"Чат: <b>{escape_truncated(payload.chat_title, 256)}</b>",
            f"Приоритет: {payload.priority.label}",
            f"SLA: {format_duration(payload.sla_seconds)}",
            f"Ответственный: {responsible}",
            "",
            f"Сообщение от: {escape_truncated(payload.author_name, 256)}",
            f"Получено: {format_datetime(payload.source_message_date, payload.timezone)}"
            f" ({escape(payload.timezone)})",
            *count_line,
            f"Просрочка: {format_duration((now - payload.deadline_at).total_seconds())}",
        ]
    )
    tail = (
        f'\n\n<a href="{escape(payload.message_link)}">Открыть сообщение</a>'
        if payload.message_link
        else ""
    )
    budget = max(
        min(SOURCE_PREVIEW_LIMIT, TELEGRAM_MESSAGE_LIMIT - len(head) - len(tail) - 200), 100
    )
    if merged:
        budget //= 2
    preview = content_preview(payload.source_text, payload.source_content_type, budget)
    body = f"Текст: {preview}"
    if merged:
        last_preview = content_preview(payload.last_message_text, payload.last_content_type, budget)
        body += f"\nПоследнее: {last_preview}"
    text = f"{head}\n\n{body}{tail}"
    return RenderedNotification(
        text=text, target_chat_id=payload.target_chat_id, target_thread_id=payload.target_thread_id
    )


def not_required_line(user_name: str, user_telegram_id: int) -> str:
    return NOT_REQUIRED_LINE.format(user=user_mention(user_telegram_id, user_name))


RESPONSIBLE_PREFIX = "Ответственный: "


def responsible_label(telegram_id: int | None, name: str | None) -> str:
    if telegram_id is None:
        return "не назначен"
    return user_mention(telegram_id, name or str(telegram_id))


def replace_responsible_line(html_text: str, telegram_id: int, name: str) -> str:
    """Point the "Ответственный:" line of a notification at a new user."""
    lines = html_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(RESPONSIBLE_PREFIX):
            lines[i] = RESPONSIBLE_PREFIX + user_mention(telegram_id, name)
            break
    return "\n".join(lines)


def reassigned_line(*, chat_scope: bool, old: str, new: str, actor: str) -> str:
    """Audit line appended to the notification; arguments are ready HTML labels."""
    if chat_scope:
        return f"👥 Ответственный чата: {old} → {new} (для новых сообщений). Изменил: {actor}"
    return f"🔁 Ответственный по сообщению: {old} → {new}. Изменил: {actor}"


def backoff_seconds(
    attempt: int, settings: CelerySettings, retry_after: float | None = None
) -> float:
    delay = float(settings.retry_backoff_base_seconds * 2 ** max(0, attempt - 1))
    delay = min(delay, float(settings.retry_backoff_max_seconds))
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


@dataclass(frozen=True, slots=True)
class _Claim:
    outcome: DeliveryOutcome | None
    payload: NotificationPayload | None = None
    attempt: int = 0


class NotificationService:
    """Delivers outbox events: claim (tx) -> send (no tx) -> record result (tx)."""

    def __init__(
        self,
        uow_factory: Callable[[], SyncUnitOfWork],
        sender: NotificationSender,
        clock: Clock,
        settings: CelerySettings,
    ) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._clock = clock
        self._settings = settings

    def deliver(self, event_id: int, *, task_id: str | None = None) -> DeliveryOutcome:
        claim = self._claim(event_id)
        if claim.outcome is not None:
            return claim.outcome
        assert claim.payload is not None
        payload = claim.payload
        log_ctx: dict[str, object] = {
            "outbox_event_id": event_id,
            "pending_reply_id": payload.pending_reply_id,
            "celery_task_id": task_id,
            "attempt": claim.attempt,
        }
        try:
            rendered = render_notification(payload, self._clock.now())
            message_id = self._sender.send_notification(
                chat_id=rendered.target_chat_id,
                thread_id=rendered.target_thread_id,
                text=rendered.text,
                pending_reply_id=payload.pending_reply_id,
            )
        except TransientDeliveryError as exc:
            return self._record_transient(event_id, claim.attempt, exc, log_ctx)
        except (PermanentDeliveryError, NotificationConfigError) as exc:
            error_type = exc.error_type if isinstance(exc, PermanentDeliveryError) else "config"
            return self._record_failure(event_id, f"{error_type}: {exc}", log_ctx)
        self._record_sent(event_id, payload, rendered, message_id)
        logger.info(
            "sla notification sent",
            extra={"event": "notification_sent", "telegram_message_id": message_id, **log_ctx},
        )
        return DeliveryOutcome(DeliveryStatus.SENT, message_id=message_id)

    def _claim(self, event_id: int) -> _Claim:
        now = self._clock.now()
        with self._uow_factory() as uow:
            event = uow.outbox.lock(event_id)
            if event is None:
                return _Claim(DeliveryOutcome(DeliveryStatus.SKIPPED, reason="missing or locked"))
            if event.status is not OutboxStatus.PENDING:
                return _Claim(DeliveryOutcome(DeliveryStatus.SKIPPED, reason=event.status.value))
            if event.available_at > now + timedelta(seconds=1):
                return _Claim(DeliveryOutcome(DeliveryStatus.SKIPPED, reason="not yet available"))
            ticket = uow.pending.get(event.aggregate_id)
            if ticket is None or ticket.status is not PendingReplyStatus.OVERDUE:
                # Answered or closed between escalation and delivery: nothing to notify.
                event.status = OutboxStatus.CANCELLED
                event.locked_at = None
                event.last_error = f"ticket status: {ticket.status.value if ticket else 'missing'}"
                uow.commit()
                return _Claim(DeliveryOutcome(DeliveryStatus.CANCELLED, reason=event.last_error))
            if event.attempts >= self._settings.max_delivery_attempts:
                self._fail(event, "max delivery attempts exceeded")
                uow.commit()
                return _Claim(DeliveryOutcome(DeliveryStatus.FAILED, reason=event.last_error))
            event.status = OutboxStatus.PROCESSING
            event.locked_at = now
            event.attempts += 1
            payload = NotificationPayload.model_validate(event.payload)
            attempt = event.attempts
            uow.commit()
        return _Claim(None, payload, attempt)

    def _record_sent(
        self,
        event_id: int,
        payload: NotificationPayload,
        rendered: RenderedNotification,
        message_id: int,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is not None:
                event.status = OutboxStatus.SENT
                event.sent_at = now
                event.locked_at = None
                event.last_error = None
                event.payload = {
                    **event.payload,
                    "notification_chat_id": rendered.target_chat_id,
                    "notification_message_id": message_id,
                }
            ticket = uow.pending.get_for_update(payload.pending_reply_id)
            if ticket is not None:
                ticket.notification_chat_id = rendered.target_chat_id
                ticket.notification_message_id = message_id
                ticket.notification_sent_at = now
            uow.commit()

    def _record_transient(
        self,
        event_id: int,
        attempt: int,
        exc: TransientDeliveryError,
        log_ctx: dict[str, object],
    ) -> DeliveryOutcome:
        if attempt >= self._settings.max_delivery_attempts:
            return self._record_failure(
                event_id, f"{exc.error_type}: {exc} (max attempts)", log_ctx
            )
        delay = backoff_seconds(attempt, self._settings, exc.retry_after)
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is not None:
                event.status = OutboxStatus.PENDING
                event.locked_at = None
                event.available_at = self._clock.now() + timedelta(seconds=delay)
                event.last_error = f"{exc.error_type}: {exc}"[:2000]
            uow.commit()
        logger.warning(
            "transient telegram error, will retry",
            extra={
                "event": "notification_retry",
                "error_type": exc.error_type,
                "error_message": str(exc),
                **log_ctx,
            },
        )
        return DeliveryOutcome(DeliveryStatus.RETRY, retry_in_seconds=delay, reason=str(exc))

    def _record_failure(
        self, event_id: int, error: str, log_ctx: dict[str, object]
    ) -> DeliveryOutcome:
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is not None:
                self._fail(event, error)
            uow.commit()
        # Technical alert: the escalation could not be delivered and needs a human.
        logger.error(
            "sla notification permanently failed",
            extra={"event": "notification_failed", "error_message": error, **log_ctx},
        )
        return DeliveryOutcome(DeliveryStatus.FAILED, reason=error)

    @staticmethod
    def _fail(event: OutboxEvent, error: str) -> None:
        event.status = OutboxStatus.FAILED
        event.locked_at = None
        event.last_error = error[:2000]
