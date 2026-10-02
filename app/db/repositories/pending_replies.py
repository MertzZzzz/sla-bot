from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import PendingReplyStatus
from app.db.models import PendingReply
from app.schemas.pending_replies import PendingReplyCreate
from app.services.reply_matching import MatchCriteria, MatchKind

OPEN_STATUSES = PendingReplyStatus.open_statuses()


def answer_candidate_stmt(
    chat_id: int, criteria: MatchCriteria, response_message_id: int
) -> Select[tuple[PendingReply]] | None:
    """Oldest open ticket matching ``criteria``; rows locked by others are skipped.

    Message IDs grow monotonically inside a chat, so ``source_message_id <
    response_message_id`` means "the response was sent after the source message".
    """
    if criteria.kind is MatchKind.NONE:
        return None
    stmt = select(PendingReply).where(
        PendingReply.chat_id == chat_id,
        PendingReply.status.in_(OPEN_STATUSES),
        PendingReply.source_message_id < response_message_id,
    )
    if criteria.kind is MatchKind.EXACT_SOURCE:
        stmt = stmt.where(PendingReply.source_message_id == criteria.source_message_id)
    elif criteria.kind is MatchKind.OLDEST_IN_THREAD:
        if criteria.thread_id is None:
            stmt = stmt.where(PendingReply.source_thread_id.is_(None))
        else:
            stmt = stmt.where(PendingReply.source_thread_id == criteria.thread_id)
    return stmt.order_by(PendingReply.source_message_id).limit(1).with_for_update(skip_locked=True)


class PendingReplyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_if_absent(self, data: PendingReplyCreate) -> int | None:
        """Idempotent insert keyed by ``(chat_id, source_message_id)``."""
        stmt = (
            insert(PendingReply)
            .values(**data.model_dump(), status=PendingReplyStatus.WAITING)
            .on_conflict_do_nothing(constraint="uq_pending_reply_source_message")
            .returning(PendingReply.id)
        )
        ticket_id: int | None = await self._session.scalar(stmt)
        return ticket_id

    async def find_for_answer(
        self, chat_id: int, criteria: MatchCriteria, response_message_id: int
    ) -> PendingReply | None:
        stmt = answer_candidate_stmt(chat_id, criteria, response_message_id)
        if stmt is None:
            return None
        ticket: PendingReply | None = await self._session.scalar(stmt)
        return ticket

    async def response_already_used(self, chat_id: int, response_message_id: int) -> bool:
        stmt = select(PendingReply.id).where(
            PendingReply.chat_id == chat_id,
            PendingReply.response_message_id == response_message_id,
        )
        return (await self._session.scalar(stmt)) is not None

    async def get(self, pending_reply_id: int) -> PendingReply | None:
        return await self._session.get(PendingReply, pending_reply_id)

    async def get_for_update(self, pending_reply_id: int) -> PendingReply | None:
        """Blocking row lock: concurrent callers are serialized, not skipped."""
        stmt = (
            select(PendingReply)
            .where(PendingReply.id == pending_reply_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        ticket: PendingReply | None = await self._session.scalar(stmt)
        return ticket

    async def list_open(self, chat_id: int, limit: int) -> Sequence[PendingReply]:
        stmt = (
            select(PendingReply)
            .where(PendingReply.chat_id == chat_id, PendingReply.status.in_(OPEN_STATUSES))
            .order_by(PendingReply.source_message_id)
            .limit(limit)
        )
        return (await self._session.scalars(stmt)).all()

    async def lock_open(self, chat_id: int) -> Sequence[PendingReply]:
        stmt = (
            select(PendingReply)
            .where(PendingReply.chat_id == chat_id, PendingReply.status.in_(OPEN_STATUSES))
            .order_by(PendingReply.id)
            .with_for_update()
        )
        return (await self._session.scalars(stmt)).all()

    async def count_open(self, chat_id: int) -> int:
        stmt = select(func.count()).where(
            PendingReply.chat_id == chat_id, PendingReply.status.in_(OPEN_STATUSES)
        )
        return int(await self._session.scalar(stmt) or 0)


class PendingReplySyncRepository:
    """Worker-side (psycopg) operations: SLA scanning and notification bookkeeping."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_due(self, now: datetime, limit: int) -> Sequence[PendingReply]:
        """Lock a batch of overdue ``waiting`` tickets; rows locked elsewhere are skipped."""
        stmt = (
            select(PendingReply)
            .where(
                PendingReply.status == PendingReplyStatus.WAITING, PendingReply.deadline_at <= now
            )
            .order_by(PendingReply.deadline_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return self._session.scalars(stmt).all()

    def get(self, pending_reply_id: int) -> PendingReply | None:
        return self._session.get(PendingReply, pending_reply_id)

    def get_for_update(self, pending_reply_id: int) -> PendingReply | None:
        stmt = (
            select(PendingReply)
            .where(PendingReply.id == pending_reply_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self._session.scalar(stmt)
