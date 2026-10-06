"""SLA warnings at a share of the SLA elapsed (e.g. 50% and 75%)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 10:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE outbox_event_type ADD VALUE IF NOT EXISTS 'sla_warning'")
    op.add_column(
        "pending_replies",
        sa.Column("warning_level", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "outbox_events",
        sa.Column("dedup_key", sa.String(length=32), server_default="", nullable=False),
    )
    op.drop_constraint("uq_outbox_event_aggregate", "outbox_events", type_="unique")
    op.create_unique_constraint(
        "uq_outbox_event_aggregate",
        "outbox_events",
        ["event_type", "aggregate_type", "aggregate_id", "dedup_key"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type = 'sla_warning'")
    op.drop_constraint("uq_outbox_event_aggregate", "outbox_events", type_="unique")
    op.create_unique_constraint(
        "uq_outbox_event_aggregate",
        "outbox_events",
        ["event_type", "aggregate_type", "aggregate_id"],
    )
    op.drop_column("outbox_events", "dedup_key")
    op.drop_column("pending_replies", "warning_level")
    # PostgreSQL cannot drop an enum value in place: recreate the type.
    op.execute("ALTER TYPE outbox_event_type RENAME TO outbox_event_type_old")
    op.execute("CREATE TYPE outbox_event_type AS ENUM ('sla_overdue_notification')")
    op.execute(
        "ALTER TABLE outbox_events ALTER COLUMN event_type TYPE outbox_event_type "
        "USING event_type::text::outbox_event_type"
    )
    op.execute("DROP TYPE outbox_event_type_old")
