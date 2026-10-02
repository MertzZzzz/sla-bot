from __future__ import annotations

import logging

from celery import Task

from app.services.notifications import DeliveryStatus
from app.workers.celery_app import QUEUE_NOTIFICATIONS, celery_app
from app.workers.context import get_worker_context

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.notifications.notify_overdue_reply",
    max_retries=None,  # attempts are bounded by the outbox (max_delivery_attempts)
)
def notify_overdue_reply(self: Task, outbox_event_id: int) -> str:  # type: ignore[type-arg]
    """Idempotent: delivery state lives in the outbox row, not in the Celery message."""
    outcome = get_worker_context().notifications.deliver(outbox_event_id, task_id=self.request.id)
    if outcome.status is DeliveryStatus.RETRY and outcome.retry_in_seconds is not None:
        # Short, bounded countdown for a retry; the periodic sweeper also picks the event
        # up if this message is lost.
        raise self.retry(countdown=outcome.retry_in_seconds)
    return outcome.status.value


@celery_app.task(bind=True, name="app.workers.tasks.notifications.deliver_pending_notifications")
def deliver_pending_notifications(self: Task) -> int:  # type: ignore[type-arg]
    """Safety net: enqueue every deliverable outbox event (lost messages, retries)."""
    event_ids = get_worker_context().sla.deliverable_event_ids()
    for event_id in event_ids:
        notify_overdue_reply.apply_async(args=(event_id,), queue=QUEUE_NOTIFICATIONS)
    if event_ids:
        logger.info(
            "pending notifications enqueued",
            extra={
                "event": "deliver_pending",
                "count": len(event_ids),
                "celery_task_id": self.request.id,
            },
        )
    return len(event_ids)
