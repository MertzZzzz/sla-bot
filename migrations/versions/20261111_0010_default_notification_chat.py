"""Global settings: default notification forum (a topic per new chat)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11 10:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("default_notification_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("default_notification_title", sa.String(length=256), nullable=True),
        sa.Column("updated_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_bot_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_settings")),
    )


def downgrade() -> None:
    op.drop_table("bot_settings")
