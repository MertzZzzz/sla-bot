from __future__ import annotations

import threading
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.enums import ChatType, ReplyMatchMode
from app.db.models import OutboxEvent, PendingReply, ReplyEvent
from app.schemas.chats import MonitoredChatUpdate
from app.services.telegram_sender import DeliveryError
from tests.factories import CHAT_ID, user

ADMIN_ID = 1000
RESPONDER = user(2000, "Responder")
CLIENT = user(3000, "Client")
NOTIFY_CHAT = -100777


async def setup_chat(
    services: BotServices,
    *,
    mode: ReplyMatchMode = ReplyMatchMode.ANY_RESPONDER_MESSAGE,
    sla_seconds: int = 900,
    chat_id: int = CHAT_ID,
) -> None:
    await services.chats.add_chat(chat_id, "Поддержка VIP", ChatType.SUPERGROUP, ADMIN_ID)
    await services.chats.update(
        chat_id,
        MonitoredChatUpdate(
            sla_seconds=sla_seconds,
            reply_match_mode=mode,
            notification_chat_id=NOTIFY_CHAT,
            notification_thread_id=5,
        ),
        ADMIN_ID,
    )
    await services.chats.set_responsible(chat_id, RESPONDER, ADMIN_ID)


def tickets(factory: sessionmaker[Session]) -> list[PendingReply]:
    with factory() as s:
        return list(s.scalars(select(PendingReply).order_by(PendingReply.id)))


def outbox(factory: sessionmaker[Session]) -> list[OutboxEvent]:
    with factory() as s:
        return list(s.scalars(select(OutboxEvent).order_by(OutboxEvent.id)))


def reply_event_types(factory: sessionmaker[Session], ticket_id: int) -> list[str]:
    with factory() as s:
        rows = s.scalars(
            select(ReplyEvent.event_type)
            .where(ReplyEvent.pending_reply_id == ticket_id)
            .order_by(ReplyEvent.id)
        )
        return [r.value for r in rows]


def count(factory: sessionmaker[Session], model: type[object]) -> int:
    with factory() as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


@dataclass
class FakeSender:
    """Records sends; optionally raises or blocks to simulate Telegram behaviour."""

    errors: list[DeliveryError] = field(default_factory=list)
    sent: list[dict[str, object]] = field(default_factory=list)
    gate: threading.Event | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def send_notification(
        self, *, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        if self.gate is not None:
            self.gate.wait(timeout=5)
        with self._lock:
            if self.errors:
                raise self.errors.pop(0)
            self.sent.append(
                {"chat_id": chat_id, "thread_id": thread_id, "text": text, "id": pending_reply_id}
            )
            return 500 + len(self.sent)
