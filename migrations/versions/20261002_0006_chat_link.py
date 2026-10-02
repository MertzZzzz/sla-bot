"""monitored_chats: public username and a link to open the chat from notifications

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02 22:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitored_chats", sa.Column("chat_username", sa.String(length=64), nullable=True))
    op.add_column("monitored_chats", sa.Column("chat_link", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("monitored_chats", "chat_link")
    op.drop_column("monitored_chats", "chat_username")
