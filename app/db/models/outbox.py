from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AggregateType, OutboxEventType, OutboxStatus
from app.db.base import Base, TimestampMixin, pg_enum


class OutboxEvent(TimestampMixin, Base):
    """Transactional outbox: written in the same transaction as the domain change."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        # At most one event of a given type (and dedup key) per aggregate: one breach
        # notification, one warning per threshold.
        UniqueConstraint(
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "dedup_key",
            name="uq_outbox_event_aggregate",
        ),
        Index(
            "ix_outbox_events_deliverable",
            "available_at",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[OutboxEventType] = mapped_column(
        pg_enum(OutboxEventType, "outbox_event_type"), nullable=False
    )
    aggregate_type: Mapped[AggregateType] = mapped_column(
        pg_enum(AggregateType, "outbox_aggregate_type"), nullable=False
    )
    aggregate_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dedup_key: Mapped[str] = mapped_column(
        String(32), default="", server_default="", nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    status: Mapped[OutboxStatus] = mapped_column(
        pg_enum(OutboxStatus, "outbox_status"),
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    available_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column()
