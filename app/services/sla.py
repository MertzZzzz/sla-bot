from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from app.core.config import CelerySettings
from app.core.enums import (
    AggregateType,
    OutboxEventType,
    OutboxStatus,
    PendingReplyStatus,
    ReplyEventType,
)
from app.db.models import MonitoredChat, PendingReply
from app.db.uow import SyncUnitOfWork
from app.schemas.notifications import NotificationPayload
from app.services.clock import Clock

logger = logging.getLogger(__name__)


class SlaService:
    """Detects SLA breaches and records escalations in the transactional outbox."""

    def __init__(
        self,
        uow_factory: Callable[[], SyncUnitOfWork],
        clock: Clock,
        settings: CelerySettings,
        warning_percents: Sequence[int] = (50, 75),
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._settings = settings
        self._warning_percents = tuple(sorted(warning_percents))

    @property
    def batch_size(self) -> int:
        return self._settings.scan_batch_size

    def escalate_due(self) -> list[int]:
        """Mark a batch of due tickets ``overdue`` and create outbox events atomically.

        Returns IDs of newly created outbox events. Tickets locked by a concurrent
        transaction (e.g. a responder answering right now) are skipped and will be
        re-evaluated on the next scan if they are still ``waiting``.
        """
        now = self._clock.now()
        created: list[int] = []
        with self._uow_factory() as uow:
            for ticket in uow.pending.claim_due(now, self._settings.scan_batch_size):
                chat = uow.chats.get(ticket.chat_id)
                if chat is None:  # pragma: no cover - FK makes this impossible
                    continue
                payload = self._build_payload(uow, ticket, chat)
                ticket.status = PendingReplyStatus.OVERDUE
                ticket.overdue_at = now
                uow.audit.add_reply_event(
                    ticket.id,
                    ReplyEventType.OVERDUE,
                    metadata={"deadline_at": ticket.deadline_at.isoformat()},
                )
                event_id = uow.outbox.add_if_absent(
                    OutboxEventType.SLA_OVERDUE_NOTIFICATION,
                    AggregateType.PENDING_REPLY,
                    ticket.id,
                    payload=payload.model_dump(mode="json"),
                    available_at=now,
                )
                if event_id is not None:
                    created.append(event_id)
            uow.commit()
        if created:
            logger.info(
                "sla breaches escalated", extra={"event": "sla_escalated", "count": len(created)}
            )
        return created

    def escalate_warnings(self) -> list[int]:
        """Queue "SLA is running out" warnings for tickets that crossed a threshold.

        Only the highest threshold reached is sent (a short SLA or a scanner delay can
        cross 50% and 75% at once); each threshold is sent at most once per ticket.
        """
        now = self._clock.now()
        created: list[int] = []
        with self._uow_factory() as uow:
            claimed = uow.pending.claim_warnings(
                now, self._warning_percents, self._settings.scan_batch_size
            )
            for ticket in claimed:
                level = self._reached_level(ticket, now)
                chat = uow.chats.get(ticket.chat_id)
                if level is None or chat is None:
                    continue
                ticket.warning_level = level
                payload = self._build_payload(uow, ticket, chat).model_copy(
                    update={"kind": "warning", "warning_percent": level}
                )
                event_id = uow.outbox.add_if_absent(
                    OutboxEventType.SLA_WARNING,
                    AggregateType.PENDING_REPLY,
                    ticket.id,
                    payload=payload.model_dump(mode="json"),
                    available_at=now,
                    dedup_key=str(level),
                )
                if event_id is not None:
                    created.append(event_id)
            uow.commit()
        if created:
            logger.info(
                "sla warnings queued", extra={"event": "sla_warning", "count": len(created)}
            )
        return created

    def _reached_level(self, ticket: PendingReply, now: datetime) -> int | None:
        window = (ticket.deadline_at - ticket.created_at).total_seconds()
        elapsed = (now - ticket.created_at).total_seconds()
        reached = [
            p
            for p in self._warning_percents
            if p > ticket.warning_level and elapsed >= window * p / 100
        ]
        return max(reached) if reached else None

    @staticmethod
    def _build_payload(
        uow: SyncUnitOfWork, ticket: PendingReply, chat: MonitoredChat
    ) -> NotificationPayload:
        responsible_name: str | None = None
        if ticket.responsible_telegram_id_snapshot is not None:
            user = uow.users.get_by_telegram_id(ticket.responsible_telegram_id_snapshot)
            responsible_name = user.display_name if user else None
        return NotificationPayload(
            pending_reply_id=ticket.id,
            target_chat_id=chat.notification_chat_id,
            target_thread_id=chat.notification_thread_id,
            chat_title=chat.title,
            priority=ticket.priority_snapshot,
            sla_seconds=ticket.sla_seconds_snapshot,
            responsible_telegram_id=ticket.responsible_telegram_id_snapshot,
            responsible_name=responsible_name,
            author_name=ticket.source_author_name or str(ticket.source_author_telegram_id),
            author_telegram_id=ticket.source_author_telegram_id,
            source_text=ticket.source_text,
            source_content_type=ticket.source_content_type,
            source_message_date=ticket.source_message_date,
            deadline_at=ticket.deadline_at,
            message_link=ticket.source_message_link,
            chat_link=chat.chat_link,
            timezone=chat.timezone,
            message_count=ticket.message_count,
            last_message_text=ticket.last_message_text,
            last_content_type=ticket.last_content_type,
            last_message_date=ticket.last_message_at,
        )

    def deliverable_event_ids(self) -> list[int]:
        with self._uow_factory() as uow:
            return list(
                uow.outbox.due_pending_ids(self._clock.now(), self._settings.delivery_batch_size)
            )

    def recover_stale_processing(self) -> list[int]:
        """Handle events whose worker died while sending.

        Telegram has no idempotency keys, so we cannot know whether the message went
        out. By default such events are failed (no duplicates) and a technical alert is
        logged; with ``resend_stale_notifications`` they are re-queued instead.
        """
        now = self._clock.now()
        timeout = timedelta(seconds=self._settings.processing_timeout_seconds)
        requeued: list[int] = []
        with self._uow_factory() as uow:
            for event in uow.outbox.lock_stale_processing(
                now, timeout, self._settings.delivery_batch_size
            ):
                event.locked_at = None
                if self._settings.resend_stale_notifications:
                    event.status = OutboxStatus.PENDING
                    event.available_at = now
                    requeued.append(event.id)
                else:
                    event.status = OutboxStatus.FAILED
                    event.last_error = "delivery state unknown: worker stopped during send"
                    logger.error(
                        "stale outbox event marked failed",
                        extra={"event": "notification_state_unknown", "outbox_event_id": event.id},
                    )
            uow.commit()
        return requeued
