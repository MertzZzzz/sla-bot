from __future__ import annotations

import logging

from celery import Task

from app.workers.celery_app import QUEUE_NOTIFICATIONS, celery_app
from app.workers.context import get_worker_context
from app.workers.tasks.notifications import notify_overdue_reply

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.workers.tasks.sla.scan_due_replies")
def scan_due_replies(self: Task) -> int:  # type: ignore[type-arg]
    """Find breached SLAs (deadline stored in PostgreSQL) and enqueue their delivery."""
    event_ids = get_worker_context().sla.escalate_due()
    for event_id in event_ids:
        notify_overdue_reply.apply_async(args=(event_id,), queue=QUEUE_NOTIFICATIONS)
    if event_ids:
        logger.info(
            "overdue notifications enqueued",
            extra={
                "event": "scan_due_replies",
                "count": len(event_ids),
                "celery_task_id": self.request.id,
            },
        )
    return len(event_ids)
