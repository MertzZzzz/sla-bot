from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PendingReplyStatus
from app.db.models import PendingReply

S = PendingReplyStatus


@dataclass(frozen=True, slots=True)
class RawChatStats:
    chat_id: int
    responsible_telegram_id: int | None
    created: int
    answered: int
    answered_within_sla: int
    overdue: int
    not_required: int
    cancelled: int
    avg_response_seconds: float | None
    median_response_seconds: float | None
    p90_response_seconds: float | None


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def period_stats(self, since: datetime, now: datetime) -> list[RawChatStats]:
        """Aggregates per (chat, responsible snapshot) for tickets created since ``since``.

        A ticket is a breach when it was answered after its deadline or is still open
        past its deadline. ``not_required``/``cancelled`` are counted separately and
        excluded from compliance.
        """
        pr = PendingReply
        answered = pr.status == S.ANSWERED
        response_seconds = extract("epoch", pr.responded_at - pr.created_at)
        breached = or_(
            and_(answered, pr.responded_at > pr.deadline_at),
            and_(pr.status.in_(S.open_statuses()), pr.deadline_at <= now),
        )
        stmt = (
            select(
                pr.chat_id,
                pr.responsible_telegram_id_snapshot,
                func.count(),
                func.count().filter(answered),
                func.count().filter(answered, pr.responded_at <= pr.deadline_at),
                func.count().filter(breached),
                func.count().filter(pr.status == S.NOT_REQUIRED),
                func.count().filter(pr.status == S.CANCELLED),
                func.avg(response_seconds).filter(answered),
                func.percentile_cont(0.5).within_group(response_seconds).filter(answered),
                func.percentile_cont(0.9).within_group(response_seconds).filter(answered),
            )
            .where(pr.created_at >= since)
            .group_by(pr.chat_id, pr.responsible_telegram_id_snapshot)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            RawChatStats(
                chat_id=r[0],
                responsible_telegram_id=r[1],
                created=r[2],
                answered=r[3],
                answered_within_sla=r[4],
                overdue=r[5],
                not_required=r[6],
                cancelled=r[7],
                avg_response_seconds=_float(r[8]),
                median_response_seconds=_float(r[9]),
                p90_response_seconds=_float(r[10]),
            )
            for r in rows
        ]

    async def open_counts(self) -> dict[tuple[int, int | None], int]:
        pr = PendingReply
        stmt = (
            select(pr.chat_id, pr.responsible_telegram_id_snapshot, func.count())
            .where(pr.status.in_(S.open_statuses()))
            .group_by(pr.chat_id, pr.responsible_telegram_id_snapshot)
        )
        return {(r[0], r[1]): r[2] for r in (await self._session.execute(stmt)).all()}


def _float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]
