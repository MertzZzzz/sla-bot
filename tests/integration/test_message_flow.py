from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.enums import PendingReplyStatus, Priority, ReplyMatchMode
from app.db.models import ChatConfigurationAudit, PendingReply
from app.schemas.chats import MonitoredChatUpdate
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.helpers import (
    ADMIN_ID,
    CLIENT,
    RESPONDER,
    count,
    reply_event_types,
    setup_chat,
    tickets,
)

pytestmark = pytest.mark.integration


async def test_external_message_creates_waiting_ticket(
    services: BotServices, sync_factory: sessionmaker[Session], clock: FakeClock
) -> None:
    await setup_chat(services, sla_seconds=600)
    result = await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    assert result.action == "created"
    [ticket] = tickets(sync_factory)
    assert ticket.status is PendingReplyStatus.WAITING
    assert ticket.source_message_id == 10
    assert ticket.source_author_telegram_id == CLIENT.telegram_user_id
    assert ticket.source_text == "Здравствуйте, есть вопрос"
    assert ticket.deadline_at == clock.now() + timedelta(seconds=600)
    assert ticket.priority_snapshot is Priority.P3
    assert ticket.sla_seconds_snapshot == 600
    assert ticket.responsible_telegram_id_snapshot == RESPONDER.telegram_user_id
    assert ticket.source_message_link == "https://t.me/c/1234567890/10"
    assert reply_event_types(sync_factory, ticket.id) == ["created"]


async def test_redelivered_update_does_not_duplicate(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    first = await services.messages.process(incoming(10, CLIENT))
    second = await services.messages.process(incoming(10, CLIENT))
    assert (first.action, second.action) == ("created", "duplicate")
    assert len(tickets(sync_factory)) == 1


async def test_unique_source_message_constraint(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    [ticket] = tickets(sync_factory)
    row = {
        c.name: getattr(ticket, c.key)
        for c in PendingReply.__table__.columns
        if c.name not in ("id", "created_at", "updated_at")
    }
    with sync_factory() as s:
        with pytest.raises(IntegrityError):
            s.execute(insert(PendingReply).values(**row))
        s.rollback()


async def test_unmonitored_and_disabled_chats_are_ignored(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    result = await services.messages.process(incoming(10, CLIENT))
    assert result.action == "ignored"
    await setup_chat(services)
    await services.chats.update(CHAT_ID, MonitoredChatUpdate(is_enabled=False), ADMIN_ID)
    result = await services.messages.process(incoming(11, CLIENT))
    assert (result.action, result.reason) == ("ignored", "monitoring disabled")
    assert tickets(sync_factory) == []


async def test_responder_answer_closes_oldest_ticket(
    services: BotServices, sync_factory: sessionmaker[Session], clock: FakeClock
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, user(3001)))
    clock.advance(minutes=3)
    result = await services.messages.process(incoming(12, RESPONDER, date=clock.now()))
    assert result.action == "answered"
    first, second = tickets(sync_factory)
    assert first.status is PendingReplyStatus.ANSWERED
    assert first.responded_at == clock.now()
    assert first.responded_by_telegram_id == RESPONDER.telegram_user_id
    assert first.response_message_id == 12
    assert second.status is PendingReplyStatus.WAITING
    assert reply_event_types(sync_factory, first.id) == ["created", "answered"]


async def test_responder_messages_never_create_tickets(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    result = await services.messages.process(incoming(10, RESPONDER))
    assert result.action == "no_match"
    assert tickets(sync_factory) == []


async def test_redelivered_responder_update_closes_only_one_ticket(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, user(3001)))
    await services.messages.process(incoming(12, RESPONDER))
    again = await services.messages.process(incoming(12, RESPONDER))
    assert again.action == "duplicate"
    statuses = [t.status for t in tickets(sync_factory)]
    assert statuses == [PendingReplyStatus.ANSWERED, PendingReplyStatus.WAITING]


async def test_reply_only_mode(services: BotServices, sync_factory: sessionmaker[Session]) -> None:
    await setup_chat(services, mode=ReplyMatchMode.REPLY_ONLY)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, user(3001)))
    plain = await services.messages.process(incoming(12, RESPONDER))
    assert plain.action == "no_match"
    reply = await services.messages.process(incoming(13, RESPONDER, reply_to=11))
    assert reply.action == "answered"
    statuses = [t.status for t in tickets(sync_factory)]
    assert statuses == [PendingReplyStatus.WAITING, PendingReplyStatus.ANSWERED]


async def test_thread_or_reply_mode(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services, mode=ReplyMatchMode.THREAD_OR_REPLY)
    await services.messages.process(
        incoming(10, CLIENT, thread_id=100, is_topic=True, reply_to=100)
    )
    await services.messages.process(
        incoming(11, CLIENT, thread_id=200, is_topic=True, reply_to=200)
    )
    # Plain message in topic 200 (Telegram sets reply_to to the topic root).
    result = await services.messages.process(
        incoming(12, RESPONDER, thread_id=200, is_topic=True, reply_to=200)
    )
    assert result.action == "answered"
    statuses = [t.status for t in tickets(sync_factory)]
    assert statuses == [PendingReplyStatus.WAITING, PendingReplyStatus.ANSWERED]


async def test_settings_change_does_not_rewrite_existing_ticket(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services, sla_seconds=900)
    await services.messages.process(incoming(10, CLIENT))
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(sla_seconds=60, priority=Priority.P1), ADMIN_ID
    )
    await services.chats.set_responsible(CHAT_ID, user(4000), ADMIN_ID)
    await services.messages.process(incoming(11, user(3001)))
    old, new = tickets(sync_factory)
    assert (old.sla_seconds_snapshot, old.priority_snapshot) == (900, Priority.P3)
    assert old.responsible_telegram_id_snapshot == RESPONDER.telegram_user_id
    assert (new.sla_seconds_snapshot, new.priority_snapshot) == (60, Priority.P1)
    assert new.responsible_telegram_id_snapshot == 4000


async def test_configuration_changes_are_audited(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.chats.add_responder(CHAT_ID, user(2001), ADMIN_ID)
    await services.chats.remove_responder(CHAT_ID, user(2001), ADMIN_ID)
    # chat_added, notification/sla/mode update, responsible, responder add + remove
    assert count(sync_factory, ChatConfigurationAudit) == 5
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    assert [u.telegram_user_id for u in details.responders] == [RESPONDER.telegram_user_id]


async def test_disabling_chat_cancels_open_tickets(
    services: BotServices, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT))
    await services.messages.process(incoming(11, user(3001)))
    await services.messages.process(incoming(12, RESPONDER))
    await services.chats.update(CHAT_ID, MonitoredChatUpdate(is_enabled=False), ADMIN_ID)
    statuses = [t.status for t in tickets(sync_factory)]
    assert statuses == [PendingReplyStatus.ANSWERED, PendingReplyStatus.CANCELLED]
    assert reply_event_types(sync_factory, tickets(sync_factory)[1].id) == ["created", "cancelled"]
    assert await services.pending.list_open(CHAT_ID) == []
