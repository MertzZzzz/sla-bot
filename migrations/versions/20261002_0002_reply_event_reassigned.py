"""reply_event_type: add 'reassigned' (responsible changed for a ticket)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02 12:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE reply_event_type ADD VALUE IF NOT EXISTS 'reassigned'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type in place: recreate the type.
    op.execute("DELETE FROM reply_events WHERE event_type = 'reassigned'")
    op.execute("ALTER TYPE reply_event_type RENAME TO reply_event_type_old")
    op.execute(
        "CREATE TYPE reply_event_type AS ENUM "
        "('created', 'answered', 'overdue', 'not_required', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE reply_events ALTER COLUMN event_type TYPE reply_event_type "
        "USING event_type::text::reply_event_type"
    )
    op.execute("DROP TYPE reply_event_type_old")
