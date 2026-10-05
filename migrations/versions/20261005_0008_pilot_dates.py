"""monitored_chats: reference pilot start/end dates

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05 14:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitored_chats", sa.Column("pilot_start", sa.Date(), nullable=True))
    op.add_column("monitored_chats", sa.Column("pilot_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("monitored_chats", "pilot_end")
    op.drop_column("monitored_chats", "pilot_start")
