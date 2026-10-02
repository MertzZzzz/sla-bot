"""merge consecutive messages of one author into a single ticket

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02 18:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE reply_event_type ADD VALUE IF NOT EXISTS 'message_added'")
    op.add_column(
        "pending_replies",
        sa.Column("message_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("pending_replies", sa.Column("last_message_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "pending_replies",
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("pending_replies", sa.Column("last_message_text", sa.Text(), nullable=True))
    op.add_column(
        "pending_replies", sa.Column("last_content_type", sa.String(length=32), nullable=True)
    )
    op.create_table(
        "pending_reply_messages",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("pending_reply_id", sa.BigInteger(), nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["monitored_chats.id"],
            name=op.f("fk_pending_reply_messages_chat_id_monitored_chats"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pending_reply_id"],
            ["pending_replies.id"],
            name=op.f("fk_pending_reply_messages_pending_reply_id_pending_replies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id", "message_id", name=op.f("pk_pending_reply_messages")),
    )
    op.create_index(
        "ix_pending_reply_messages_pending_reply_id",
        "pending_reply_messages",
        ["pending_reply_id"],
    )
    op.execute(
        """
        INSERT INTO pending_reply_messages (chat_id, message_id, pending_reply_id, message_date)
        SELECT chat_id, source_message_id, id, source_message_date FROM pending_replies
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_reply_messages_pending_reply_id", table_name="pending_reply_messages"
    )
    op.drop_table("pending_reply_messages")
    for column in (
        "last_content_type",
        "last_message_text",
        "last_message_at",
        "last_message_id",
        "message_count",
    ):
        op.drop_column("pending_replies", column)
    # PostgreSQL cannot drop an enum value in place: recreate the type.
    op.execute("DELETE FROM reply_events WHERE event_type = 'message_added'")
    op.execute("ALTER TYPE reply_event_type RENAME TO reply_event_type_old")
    op.execute(
        "CREATE TYPE reply_event_type AS ENUM "
        "('created', 'answered', 'overdue', 'not_required', 'cancelled', 'reassigned')"
    )
    op.execute(
        "ALTER TABLE reply_events ALTER COLUMN event_type TYPE reply_event_type "
        "USING event_type::text::reply_event_type"
    )
    op.execute("DROP TYPE reply_event_type_old")
