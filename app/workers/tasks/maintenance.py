from __future__ import annotations

from celery import Task

from app.workers.celery_app import QUEUE_NOTIFICATIONS, celery_app
from app.workers.context import get_worker_context
from app.workers.tasks.notifications import notify_overdue_reply


@celery_app.task(bind=True, name="app.workers.tasks.maintenance.recover_stale_notifications")
def recover_stale_notifications(self: Task) -> int:  # type: ignore[type-arg]
    requeued = get_worker_context().sla.recover_stale_processing()
    for event_id in requeued:
        notify_overdue_reply.apply_async(args=(event_id,), queue=QUEUE_NOTIFICATIONS)
    return len(requeued)
