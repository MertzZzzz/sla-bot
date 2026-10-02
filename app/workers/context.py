"""Per-process worker dependencies, created lazily *after* the prefork fork."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.bot.sender import AiogramNotificationSender
from app.core.config import get_settings
from app.db.session import build_sync_engine, build_sync_sessionmaker
from app.db.uow import SyncUnitOfWork
from app.services.clock import SystemClock
from app.services.notifications import NotificationService
from app.services.sla import SlaService


@dataclass(frozen=True, slots=True)
class WorkerContext:
    sla: SlaService
    notifications: NotificationService


@lru_cache(maxsize=1)
def get_worker_context() -> WorkerContext:
    settings = get_settings()
    session_factory = build_sync_sessionmaker(build_sync_engine(settings.database))

    def uow_factory() -> SyncUnitOfWork:
        return SyncUnitOfWork(session_factory)

    clock = SystemClock()
    return WorkerContext(
        sla=SlaService(uow_factory, clock, settings.celery),
        notifications=NotificationService(
            uow_factory, AiogramNotificationSender(settings.telegram), clock, settings.celery
        ),
    )
