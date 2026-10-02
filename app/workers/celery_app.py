from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging
from kombu import Exchange, Queue

from app.core.config import get_settings
from app.core.logging import configure_logging

QUEUE_DEFAULT = "default"
QUEUE_NOTIFICATIONS = "notifications"
QUEUE_MAINTENANCE = "maintenance"


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "sla_bot",
        include=[
            "app.workers.tasks.sla",
            "app.workers.tasks.notifications",
            "app.workers.tasks.maintenance",
        ],
    )
    cs = settings.celery
    app.conf.update(
        broker_url=settings.redis.broker_url,
        result_backend=settings.redis.result_backend_url,
        task_default_queue=QUEUE_DEFAULT,
        task_queues=[
            Queue(QUEUE_DEFAULT, Exchange(QUEUE_DEFAULT), routing_key=QUEUE_DEFAULT),
            Queue(
                QUEUE_NOTIFICATIONS, Exchange(QUEUE_NOTIFICATIONS), routing_key=QUEUE_NOTIFICATIONS
            ),
            Queue(QUEUE_MAINTENANCE, Exchange(QUEUE_MAINTENANCE), routing_key=QUEUE_MAINTENANCE),
        ],
        task_routes={
            "app.workers.tasks.sla.*": {"queue": QUEUE_DEFAULT},
            "app.workers.tasks.notifications.*": {"queue": QUEUE_NOTIFICATIONS},
            "app.workers.tasks.maintenance.*": {"queue": QUEUE_MAINTENANCE},
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=cs.task_soft_time_limit_seconds,
        task_time_limit=cs.task_time_limit_seconds,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        # Periodic scans are idempotent; a short expiry keeps a backlog from piling up
        # while workers are down.
        beat_schedule={
            "scan-due-replies": {
                "task": "app.workers.tasks.sla.scan_due_replies",
                "schedule": cs.scan_interval_seconds,
                "options": {"expires": cs.scan_interval_seconds},
            },
            "deliver-pending-notifications": {
                "task": "app.workers.tasks.notifications.deliver_pending_notifications",
                "schedule": cs.delivery_sweep_interval_seconds,
                "options": {"expires": cs.delivery_sweep_interval_seconds},
            },
            "recover-stale-notifications": {
                "task": "app.workers.tasks.maintenance.recover_stale_notifications",
                "schedule": float(cs.processing_timeout_seconds),
                "options": {"expires": float(cs.processing_timeout_seconds)},
            },
        },
    )
    return app


@setup_logging.connect
def _configure_logging(**_: object) -> None:
    configure_logging(get_settings().logging)


celery_app = create_celery()
