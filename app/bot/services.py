from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.uow import UnitOfWork
from app.services.admins import AdminService
from app.services.audit import AuditService
from app.services.chat_settings import ChatSettingsService
from app.services.clock import Clock, SystemClock
from app.services.message_links import MessageLinkService
from app.services.message_processing import MessageProcessingService
from app.services.pending_replies import PendingReplyService
from app.services.reassignment import ReassignmentService
from app.services.stats import StatsService
from app.services.users import TelegramUserService


@dataclass(frozen=True, slots=True)
class BotServices:
    """Stateless services shared by all handlers (injected via dispatcher workflow data)."""

    messages: MessageProcessingService
    pending: PendingReplyService
    chats: ChatSettingsService
    stats: StatsService
    users: TelegramUserService
    reassignment: ReassignmentService
    admins: AdminService


def build_bot_services(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock | None = None,
) -> BotServices:
    clock = clock or SystemClock()

    def uow_factory() -> UnitOfWork:
        return UnitOfWork(session_factory)

    pending = PendingReplyService(
        uow_factory,
        clock,
        MessageLinkService(),
        responsible_can_mark_not_required=settings.app.responsible_can_mark_not_required,
        merge_consecutive_messages=settings.app.merge_consecutive_messages,
    )
    chats = ChatSettingsService(uow_factory, clock, AuditService(), settings.app)
    return BotServices(
        messages=MessageProcessingService(uow_factory, pending, clock),
        pending=pending,
        chats=chats,
        stats=StatsService(uow_factory, clock),
        users=TelegramUserService(uow_factory, clock),
        reassignment=ReassignmentService(uow_factory, chats),
        admins=AdminService(uow_factory, clock, settings.telegram.admin_telegram_ids),
    )
