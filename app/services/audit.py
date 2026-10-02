from __future__ import annotations

from typing import Any

from app.core.enums import ChatConfigAction
from app.db.uow import UnitOfWork


class AuditService:
    """Records configuration changes in the same transaction as the change itself."""

    async def record_chat_change(
        self,
        uow: UnitOfWork,
        *,
        chat_id: int,
        actor_telegram_user_id: int,
        action: ChatConfigAction,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
    ) -> None:
        await uow.audit.add_chat_change(
            chat_id, actor_telegram_user_id, action, old_value, new_value
        )
