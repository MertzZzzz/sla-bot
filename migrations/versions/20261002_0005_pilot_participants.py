"""pilot participants: people invited into customer chats with /invite_all

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02 20:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pilot_participants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("added_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "username IS NOT NULL OR telegram_user_id IS NOT NULL",
            name=op.f("ck_pilot_participants_identified"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pilot_participants")),
    )
    op.create_index(
        "uq_pilot_participants_username",
        "pilot_participants",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_index(
        "uq_pilot_participants_telegram_user_id",
        "pilot_participants",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("telegram_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_pilot_participants_telegram_user_id", table_name="pilot_participants")
    op.drop_index("uq_pilot_participants_username", table_name="pilot_participants")
    op.drop_table("pilot_participants")
