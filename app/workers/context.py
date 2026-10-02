"""Per-process worker dependencies, created lazily *after* the prefork fork."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from app.bot.sender import AiogramNotificationSender
from app.core.config import get_settings
from app.db.session import build_sync_engine, build_sync_sessionmaker
from app.db.uow import SyncUnitOfWork
from app.services.clock import SystemClock
from app.services.notifications import NotificationService
from app.services.sla import SlaService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerContext:
    sla: SlaService
    notifications: NotificationService


@lru_cache(maxsize=1)
def get_worker_context() -> WorkerContext:
    settings = get_settings()
    if settings.telegram.proxy_display:
        logger.info(
            f"telegram api proxy: {settings.telegram.proxy_display}",
            extra={"event": "telegram_proxy_enabled"},
        )
    session_factory = build_sync_sessionmaker(build_sync_engine(settings.database))

    def uow_factory() -> SyncUnitOfWork:
        return SyncUnitOfWork(session_factory)

    clock = SystemClock()

    def alert_recipients() -> list[int]:
        """Global admins (.env and menu-added) privately, plus admin chats."""
        with uow_factory() as uow:
            admins = set(uow.admins.telegram_ids())
        return [
            *sorted(admins | settings.telegram.admin_telegram_ids),
            *settings.telegram.admin_chat_ids,
        ]

    return WorkerContext(
        sla=SlaService(uow_factory, clock, settings.celery),
        notifications=NotificationService(
            uow_factory,
            AiogramNotificationSender(settings.telegram),
            clock,
            settings.celery,
            alert_recipients,
        ),
    )
