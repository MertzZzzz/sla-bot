"""settings menu: chat_members (known chat participants) and bot_admins

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02 14:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_members",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["monitored_chats.id"],
            name=op.f("fk_chat_members_chat_id_monitored_chats"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_users.id"],
            name=op.f("fk_chat_members_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id", "user_id", name=op.f("pk_chat_members")),
    )
    op.create_index(
        "ix_chat_members_chat_last_seen", "chat_members", ["chat_id", "last_seen_at"]
    )
    op.create_table(
        "bot_admins",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("notify_new_chats", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("added_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_users.id"],
            name=op.f("fk_bot_admins_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("telegram_user_id", name=op.f("pk_bot_admins")),
    )
    # Seed from what is already known: ticket authors, people who answered,
    # responders and responsibles.
    op.execute(
        """
        INSERT INTO chat_members (chat_id, user_id, first_seen_at, last_seen_at)
        SELECT chat_id, user_id, min(seen), max(seen) FROM (
            SELECT chat_id, source_author_user_id AS user_id, source_message_date AS seen
              FROM pending_replies WHERE source_author_user_id IS NOT NULL
            UNION ALL
            SELECT chat_id, responded_by_user_id, responded_at
              FROM pending_replies WHERE responded_by_user_id IS NOT NULL
            UNION ALL
            SELECT chat_id, user_id, created_at FROM chat_responders
            UNION ALL
            SELECT id, responsible_user_id, updated_at
              FROM monitored_chats WHERE responsible_user_id IS NOT NULL
        ) AS seen_users
        GROUP BY chat_id, user_id
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("bot_admins")
    op.drop_index("ix_chat_members_chat_last_seen", table_name="chat_members")
    op.drop_table("chat_members")
