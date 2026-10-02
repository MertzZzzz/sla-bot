from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import ChatConfigAction, ReplyEventType
from app.db.models import ChatConfigurationAudit, ReplyEvent


def _reply_event(
    pending_reply_id: int,
    event_type: ReplyEventType,
    actor_telegram_user_id: int | None,
    telegram_message_id: int | None,
    metadata: dict[str, Any] | None,
) -> ReplyEvent:
    return ReplyEvent(
        pending_reply_id=pending_reply_id,
        event_type=event_type,
        actor_telegram_user_id=actor_telegram_user_id,
        telegram_message_id=telegram_message_id,
        metadata_=metadata or {},
    )


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_reply_event(
        self,
        pending_reply_id: int,
        event_type: ReplyEventType,
        *,
        actor_telegram_user_id: int | None = None,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            _reply_event(
                pending_reply_id, event_type, actor_telegram_user_id, telegram_message_id, metadata
            )
        )
        await self._session.flush()

    async def add_chat_change(
        self,
        chat_id: int,
        actor_telegram_user_id: int,
        action: ChatConfigAction,
        old_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
    ) -> None:
        self._session.add(
            ChatConfigurationAudit(
                chat_id=chat_id,
                actor_telegram_user_id=actor_telegram_user_id,
                action=action.value,
                old_value=old_value,
                new_value=new_value,
            )
        )
        await self._session.flush()


class SyncAuditRepository:
    """Worker-side (psycopg) variant used by Celery tasks."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_reply_event(
        self,
        pending_reply_id: int,
        event_type: ReplyEventType,
        *,
        actor_telegram_user_id: int | None = None,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            _reply_event(
                pending_reply_id, event_type, actor_telegram_user_id, telegram_message_id, metadata
            )
        )
        self._session.flush()
