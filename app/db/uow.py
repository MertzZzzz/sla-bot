"""Units of work: one session + transaction + repositories per business operation.

Services open a unit of work, do their DB work, commit, and only *then* talk to
Telegram, so no transaction is held open during network I/O.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.db.repositories.admins import AdminRepository, SyncAdminRepository
from app.db.repositories.audit import AuditRepository, SyncAuditRepository
from app.db.repositories.chats import (
    ChatMemberRepository,
    MonitoredChatRepository,
    ResponderRepository,
    SyncMonitoredChatRepository,
)
from app.db.repositories.outbox import AsyncOutboxRepository, OutboxRepository
from app.db.repositories.pending_replies import PendingReplyRepository, PendingReplySyncRepository
from app.db.repositories.pilot import PilotRepository
from app.db.repositories.stats import StatsRepository
from app.db.repositories.users import SyncUserRepository, UserRepository


class UnitOfWork:
    session: AsyncSession
    users: UserRepository
    chats: MonitoredChatRepository
    responders: ResponderRepository
    members: ChatMemberRepository
    pending: PendingReplyRepository
    audit: AuditRepository
    stats: StatsRepository
    admins: AdminRepository
    pilot: PilotRepository
    outbox: AsyncOutboxRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self.users = UserRepository(self.session)
        self.chats = MonitoredChatRepository(self.session)
        self.responders = ResponderRepository(self.session)
        self.members = ChatMemberRepository(self.session)
        self.pending = PendingReplyRepository(self.session)
        self.audit = AuditRepository(self.session)
        self.stats = StatsRepository(self.session)
        self.admins = AdminRepository(self.session)
        self.pilot = PilotRepository(self.session)
        self.outbox = AsyncOutboxRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()  # no-op after a successful commit
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()


class SyncUnitOfWork:
    session: Session
    pending: PendingReplySyncRepository
    outbox: OutboxRepository
    audit: SyncAuditRepository
    chats: SyncMonitoredChatRepository
    users: SyncUserRepository
    admins: SyncAdminRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def __enter__(self) -> Self:
        self.session = self._session_factory()
        self.pending = PendingReplySyncRepository(self.session)
        self.outbox = OutboxRepository(self.session)
        self.audit = SyncAuditRepository(self.session)
        self.chats = SyncMonitoredChatRepository(self.session)
        self.users = SyncUserRepository(self.session)
        self.admins = SyncAdminRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self.session.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        self.session.commit()
