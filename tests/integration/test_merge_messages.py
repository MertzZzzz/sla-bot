"""Consecutive messages of one author need one answer, not one per message."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices, build_bot_services
from app.core.config import Settings
from app.core.enums import ContentType, PendingReplyStatus, ReplyMatchMode
from app.db.models import PendingReplyMessage
from app.db.uow import SyncUnitOfWork
from app.services.notifications import NotificationService
from app.services.sla import SlaService
from tests.factories import FakeClock, incoming, user
from tests.integration.helpers import (
    CLIENT,
    RESPONDER,
    FakeSender,
    outbox,
    reply_event_types,
    setup_chat,
    tickets,
)

pytestmark = pytest.mark.integration


def linked_messages(factory: sessionmaker[Session], ticket_id: int) -> list[int]:
    with factory() as s:
        return list(
            s.scalars(
                select(PendingReplyMessage.message_id)
                .where(PendingReplyMessage.pending_reply_id == ticket_id)
                .order_by(PendingReplyMessage.message_id)
            )
        )


async def test_consecutive_messages_form_one_ticket(
    services: BotServices, sync_factory: sessionmaker[Session], clock: FakeClock
) -> None:
    await setup_chat(services, sla_seconds=600)
    first = await services.messages.process(
        incoming(10, CLIENT, text="Добрый день", date=clock.now())
    )
    start = clock.now()
    clock.advance(minutes=2)
    second = await services.messages.process(
        incoming(11, CLIENT, text="У нас проблема", date=clock.now())
    )
    clock.advance(minutes=1)
    photo = incoming(12, CLIENT, text=None, content_type=ContentType.PHOTO, date=clock.now())
    third = await services.messages.process(photo)

    assert (first.action, second.action, third.action) == ("created", "merged", "merged")
    [ticket] = tickets(sync_factory)
    assert ticket.message_count == 3
    assert ticket.source_text == "Добрый день"
    assert ticket.last_message_id == 12
    assert ticket.last_content_type == "photo"
    assert ticket.last_message_at == clock.now()
    assert ticket.deadline_at == start + timedelta(minutes=10)  # anchored to the first one
    assert linked_messages(sync_factory, ticket.id) == [10, 11, 12]
    assert reply_event_types(sync_factory, ticket.id) == [
        "created",
        "message_added",
        "message_added",
    ]

    answer = await services.messages.process(incoming(13, RESPONDER, date=clock.now()))
    assert answer.action == "answered"
    assert tickets(sync_factory)[0].status is PendingReplyStatus.ANSWERED


async def test_other_authors_and_topics_are_separate(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, user(3001)))  # another customer
    await services.messages.process(incoming(12, CLIENT))  # still merges into #10
    await services.messages.process(
        incoming(13, CLIENT, thread_id=200, is_topic=True, reply_to=200)
    )
    counts = [(t.source_message_id, t.message_count) for t in tickets(sync_factory)]
    assert counts == [(10, 2), (11, 1), (13, 1)]


async def test_message_after_answer_opens_new_ticket(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, RESPONDER))
    result = await services.messages.process(incoming(12, CLIENT))
    assert result.action == "created"
    statuses = [t.status for t in tickets(sync_factory)]
    assert statuses == [PendingReplyStatus.ANSWERED, PendingReplyStatus.WAITING]


async def test_reply_to_any_merged_message_closes_ticket(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services, mode=ReplyMatchMode.REPLY_ONLY)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, CLIENT))
    result = await services.messages.process(incoming(12, RESPONDER, reply_to=11))
    assert result.action == "answered"
    assert tickets(sync_factory)[0].status is PendingReplyStatus.ANSWERED


async def test_redelivered_merged_message_is_duplicate(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, CLIENT))
    again = await services.messages.process(incoming(11, CLIENT))
    first_again = await services.messages.process(incoming(10, CLIENT))
    assert (again.action, first_again.action) == ("duplicate", "duplicate")
    assert tickets(sync_factory)[0].message_count == 2


async def test_concurrent_messages_of_one_author_create_one_ticket(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    results = await asyncio.gather(
        *(services.messages.process(incoming(10 + i, CLIENT)) for i in range(5))
    )
    assert sorted(r.action for r in results).count("created") == 1
    [ticket] = tickets(sync_factory)
    assert ticket.message_count == 5
    assert len(linked_messages(sync_factory, ticket.id)) == 5


async def test_merge_into_overdue_ticket_does_not_notify_again(
    services: BotServices,
    sync_factory: sessionmaker[Session],
    sync_uow: Callable[[], SyncUnitOfWork],
    clock: FakeClock,
    settings: Settings,
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, text="Вопрос", date=clock.now()))
    await services.messages.process(incoming(11, CLIENT, text="Уточнение", date=clock.now()))
    clock.advance(seconds=901)
    sla = SlaService(sync_uow, clock, settings.celery)
    sender = FakeSender()
    [event_id] = sla.escalate_due()
    NotificationService(sync_uow, sender, clock, settings.celery).deliver(event_id)
    text = str(sender.sent[0]["text"])
    assert "Сообщений: 2" in text
    assert "Текст: Вопрос" in text
    assert "Последнее: Уточнение" in text

    result = await services.messages.process(incoming(12, CLIENT, date=clock.now()))
    assert result.action == "merged"
    assert sla.escalate_due() == []
    assert len(outbox(sync_factory)) == 1


async def test_merging_can_be_disabled(
    settings: Settings,
    async_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    off = settings.model_copy(
        update={"app": settings.app.model_copy(update={"merge_consecutive_messages": False})}
    )
    services = build_bot_services(off, async_factory, clock)
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, CLIENT))
    assert [t.message_count for t in tickets(sync_factory)] == [1, 1]
