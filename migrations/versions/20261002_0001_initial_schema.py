"""initial schema: core tables, enum types and indexes

Revision ID: 0001
Revises:
Create Date: 2026-10-02 09:59:49.098851+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "chat_type": ("group", "supergroup"),
    "chat_priority": ("p1", "p2", "p3", "p4"),
    "reply_match_mode": ("any_responder_message", "reply_only", "thread_or_reply"),
    "pending_reply_status": ("waiting", "overdue", "answered", "not_required", "cancelled"),
    "reply_event_type": ("created", "answered", "overdue", "not_required", "cancelled"),
    "outbox_status": ("pending", "processing", "sent", "failed", "cancelled"),
    "outbox_event_type": ("sla_overdue_notification",),
    "outbox_aggregate_type": ("pending_reply",),
}
# Types are created explicitly once; columns reference them with create_type=False.
ENUMS = {
    name: postgresql.ENUM(*values, name=name, create_type=False)
    for name, values in ENUM_VALUES.items()
}


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUM_VALUES.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", ENUMS["outbox_event_type"], nullable=False),
        sa.Column("aggregate_type", ENUMS["outbox_aggregate_type"], nullable=False),
        sa.Column("aggregate_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", ENUMS["outbox_status"], server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint(
            "event_type", "aggregate_type", "aggregate_id", name="uq_outbox_event_aggregate"
        ),
    )
    op.create_index(
        "ix_outbox_events_deliverable",
        "outbox_events",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=256), nullable=True),
        sa.Column("last_name", sa.String(length=256), nullable=True),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("is_bot", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_users")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_telegram_users_telegram_user_id")),
    )
    op.create_table(
        "monitored_chats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("chat_type", ENUMS["chat_type"], nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("priority", ENUMS["chat_priority"], nullable=False),
        sa.Column("sla_seconds", sa.Integer(), nullable=False),
        sa.Column("reply_match_mode", ENUMS["reply_match_mode"], nullable=False),
        sa.Column("responsible_user_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="Europe/Moscow", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sla_seconds > 0", name=op.f("ck_monitored_chats_sla_seconds_positive")),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_monitored_chats_responsible_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitored_chats")),
        sa.UniqueConstraint("telegram_chat_id", name=op.f("uq_monitored_chats_telegram_chat_id")),
    )
    op.create_table(
        "chat_configuration_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["monitored_chats.id"],
            name=op.f("fk_chat_configuration_audit_chat_id_monitored_chats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_configuration_audit")),
    )
    op.create_index(
        "ix_chat_configuration_audit_chat_id", "chat_configuration_audit", ["chat_id"], unique=False
    )
    op.create_table(
        "chat_responders",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["monitored_chats.id"],
            name=op.f("fk_chat_responders_chat_id_monitored_chats"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_users.id"],
            name=op.f("fk_chat_responders_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id", "user_id", name=op.f("pk_chat_responders")),
    )
    op.create_table(
        "pending_replies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("source_author_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source_author_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("source_author_name", sa.String(length=512), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("source_content_type", sa.String(length=32), nullable=True),
        sa.Column("source_message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_message_link", sa.String(length=256), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority_snapshot", ENUMS["chat_priority"], nullable=False),
        sa.Column("sla_seconds_snapshot", sa.Integer(), nullable=False),
        sa.Column("responsible_telegram_id_snapshot", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", ENUMS["pending_reply_status"], server_default="waiting", nullable=False
        ),
        sa.Column("overdue_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("responded_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("response_message_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_message_id", sa.BigInteger(), nullable=True),
        sa.Column("notification_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_required_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("not_required_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sla_seconds_snapshot > 0", name=op.f("ck_pending_replies_sla_snapshot_positive")
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["monitored_chats.id"],
            name=op.f("fk_pending_replies_chat_id_monitored_chats"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["not_required_by_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_pending_replies_not_required_by_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["responded_by_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_pending_replies_responded_by_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_author_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_pending_replies_source_author_user_id_telegram_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_replies")),
        sa.UniqueConstraint("chat_id", "source_message_id", name="uq_pending_reply_source_message"),
    )
    op.create_index(
        "ix_pending_reply_chat_status_created",
        "pending_replies",
        ["chat_id", "status", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_pending_reply_created_at", "pending_replies", ["created_at"], unique=False)
    op.create_index(
        "ix_pending_reply_due_waiting",
        "pending_replies",
        ["deadline_at"],
        unique=False,
        postgresql_where=sa.text("status = 'waiting'"),
    )
    op.create_index(
        "ix_pending_reply_responder_stats",
        "pending_replies",
        ["responded_by_telegram_id", "responded_at"],
        unique=False,
        postgresql_where=sa.text("status = 'answered'"),
    )
    op.create_index(
        "uq_pending_reply_response_message",
        "pending_replies",
        ["chat_id", "response_message_id"],
        unique=True,
        postgresql_where=sa.text("response_message_id IS NOT NULL"),
    )
    op.create_table(
        "reply_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pending_reply_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", ENUMS["reply_event_type"], nullable=False),
        sa.Column("actor_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pending_reply_id"],
            ["pending_replies.id"],
            name=op.f("fk_reply_events_pending_reply_id_pending_replies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reply_events")),
    )
    op.create_index(
        "ix_reply_events_pending_reply_id", "reply_events", ["pending_reply_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_reply_events_pending_reply_id", table_name="reply_events")
    op.drop_table("reply_events")
    op.drop_index(
        "uq_pending_reply_response_message",
        table_name="pending_replies",
        postgresql_where=sa.text("response_message_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_pending_reply_responder_stats",
        table_name="pending_replies",
        postgresql_where=sa.text("status = 'answered'"),
    )
    op.drop_index(
        "ix_pending_reply_due_waiting",
        table_name="pending_replies",
        postgresql_where=sa.text("status = 'waiting'"),
    )
    op.drop_index("ix_pending_reply_created_at", table_name="pending_replies")
    op.drop_index("ix_pending_reply_chat_status_created", table_name="pending_replies")
    op.drop_table("pending_replies")
    op.drop_table("chat_responders")
    op.drop_index("ix_chat_configuration_audit_chat_id", table_name="chat_configuration_audit")
    op.drop_table("chat_configuration_audit")
    op.drop_table("monitored_chats")
    op.drop_table("telegram_users")
    op.drop_index(
        "ix_outbox_events_deliverable",
        table_name="outbox_events",
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.drop_table("outbox_events")
    bind = op.get_bind()
    for name in reversed(ENUM_VALUES):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
