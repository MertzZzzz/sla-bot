"""customer chat can be a forum topic; registry of seen forum topics

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 10:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitored_chats", sa.Column("thread_id", sa.BigInteger(), nullable=True))
    op.add_column("monitored_chats", sa.Column("topic_name", sa.String(length=128), nullable=True))
    op.drop_constraint(
        "uq_monitored_chats_telegram_chat_id", "monitored_chats", type_="unique"
    )
    op.create_index(
        "uq_monitored_chats_chat_thread",
        "monitored_chats",
        ["telegram_chat_id", sa.text("coalesce(thread_id, 0)")],
        unique=True,
    )
    op.create_table(
        "forum_topics",
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("telegram_chat_id", "thread_id", name=op.f("pk_forum_topics")),
    )


def downgrade() -> None:
    op.drop_table("forum_topics")
    # Topic-level chats cannot be represented without thread_id.
    op.execute("DELETE FROM monitored_chats WHERE thread_id IS NOT NULL")
    op.drop_index("uq_monitored_chats_chat_thread", table_name="monitored_chats")
    op.create_unique_constraint(
        "uq_monitored_chats_telegram_chat_id", "monitored_chats", ["telegram_chat_id"]
    )
    op.drop_column("monitored_chats", "topic_name")
    op.drop_column("monitored_chats", "thread_id")
