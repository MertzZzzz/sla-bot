from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.enums import AggregateType, OutboxEventType, OutboxStatus
from app.db.models import OutboxEvent


class OutboxRepository:
    """Worker-side transactional outbox."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_if_absent(
        self,
        event_type: OutboxEventType,
        aggregate_type: AggregateType,
        aggregate_id: int,
        payload: dict[str, Any],
        available_at: datetime,
    ) -> int | None:
        stmt = (
            insert(OutboxEvent)
            .values(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status=OutboxStatus.PENDING,
                attempts=0,
                available_at=available_at,
            )
            .on_conflict_do_nothing(constraint="uq_outbox_event_aggregate")
            .returning(OutboxEvent.id)
        )
        return self._session.scalar(stmt)

    def lock(self, event_id: int) -> OutboxEvent | None:
        """Lock one event or return ``None`` if it is missing or locked by another worker."""
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return self._session.scalar(stmt)

    def get(self, event_id: int) -> OutboxEvent | None:
        return self._session.get(OutboxEvent, event_id)

    def due_pending_ids(self, now: datetime, limit: int) -> Sequence[int]:
        stmt = (
            select(OutboxEvent.id)
            .where(OutboxEvent.status == OutboxStatus.PENDING, OutboxEvent.available_at <= now)
            .order_by(OutboxEvent.available_at)
            .limit(limit)
        )
        return self._session.scalars(stmt).all()

    def lock_stale_processing(
        self, now: datetime, timeout: timedelta, limit: int
    ) -> Sequence[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at <= now - timeout),
            )
            .order_by(OutboxEvent.locked_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return self._session.scalars(stmt).all()
