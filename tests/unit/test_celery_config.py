from __future__ import annotations

import pytest

from app.workers.celery_app import celery_app
from app.workers.tasks import notifications as notifications_task
from app.workers.tasks import sla as sla_task


def test_reliability_settings() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_track_started is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_soft_time_limit < conf.task_time_limit
    assert {q.name for q in conf.task_queues} == {"default", "notifications", "maintenance"}


def test_beat_schedule_uses_periodic_scanner() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["scan-due-replies"]["task"] == "app.workers.tasks.sla.scan_due_replies"
    assert schedule["scan-due-replies"]["schedule"] == 30.0
    assert "deliver-pending-notifications" in schedule


def test_tasks_registered_and_routed() -> None:
    import app.workers.tasks.maintenance  # noqa: F401

    names = set(celery_app.tasks)
    for name in (
        "app.workers.tasks.sla.scan_due_replies",
        "app.workers.tasks.notifications.notify_overdue_reply",
        "app.workers.tasks.notifications.deliver_pending_notifications",
        "app.workers.tasks.maintenance.recover_stale_notifications",
    ):
        assert name in names
    route = celery_app.amqp.router.route({}, "app.workers.tasks.notifications.notify_overdue_reply")
    assert route["queue"].name == "notifications"


def test_scan_task_drains_backlog_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSla:
        batch_size = 2

        def __init__(self) -> None:
            self.batches = [[1, 2], [3, 4], [5]]

        def escalate_due(self) -> list[int]:
            return self.batches.pop(0) if self.batches else []

        def escalate_warnings(self) -> list[int]:
            return [9]

    class Ctx:
        sla = FakeSla()

    enqueued: list[tuple[int, ...]] = []
    monkeypatch.setattr(sla_task, "get_worker_context", Ctx)
    monkeypatch.setattr(
        notifications_task.notify_overdue_reply,
        "apply_async",
        lambda args, queue: enqueued.append(args),
    )
    assert sla_task.scan_due_replies.apply().get() == 6
    assert enqueued == [(9,), (1,), (2,), (3,), (4,), (5,)]  # warnings first
