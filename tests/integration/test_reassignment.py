from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.enums import ChatConfigAction
from app.db.models import ChatConfigurationAudit
from app.schemas.stats import StatsQuery
from app.services.reassignment import ReassignOutcome, ReassignScope
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.helpers import (
    ADMIN_ID,
    CLIENT,
    RESPONDER,
    reply_event_types,
    setup_chat,
    tickets,
)

pytestmark = pytest.mark.integration

ADMIN = user(ADMIN_ID, "Admin")
COLLEAGUE = user(2001, "Colleague")
STRANGER = user(9999, "Stranger")
TICKET = ReassignScope.TICKET
CHAT = ReassignScope.CHAT


@pytest.fixture
async def ticket_id(services: BotServices, clock: FakeClock) -> int:
    await setup_chat(services)  # RESPONDER is responsible and a responder
    await services.chats.add_responder(CHAT_ID, COLLEAGUE, ADMIN_ID)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    return 1


async def test_options_exclude_current_responsible(services: BotServices, ticket_id: int) -> None:
    for scope in (TICKET, CHAT):
        options = await services.reassignment.options(ticket_id, scope, ADMIN_ID, is_admin=True)
        assert options.outcome is ReassignOutcome.OK
        assert [u.telegram_user_id for u in options.candidates] == [COLLEAGUE.telegram_user_id]


async def test_admin_reassigns_ticket(
    services: BotServices, ticket_id: int, sync_factory: sessionmaker[Session]
) -> None:
    result = await services.reassignment.reassign(
        ticket_id, TICKET, COLLEAGUE.telegram_user_id, ADMIN, is_admin=True
    )
    assert result.outcome is ReassignOutcome.OK
    assert result.old is not None and result.old.telegram_user_id == RESPONDER.telegram_user_id
    assert result.new is not None and result.new.telegram_user_id == COLLEAGUE.telegram_user_id
    [ticket] = tickets(sync_factory)
    assert ticket.responsible_telegram_id_snapshot == COLLEAGUE.telegram_user_id
    assert reply_event_types(sync_factory, ticket_id) == ["created", "reassigned"]
    # Chat-level responsible is untouched; statistics follow the ticket.
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and details.responsible is not None
    assert details.responsible.telegram_user_id == RESPONDER.telegram_user_id
    [row] = await services.stats.collect(StatsQuery(days=1))
    assert row.responsible_telegram_id == COLLEAGUE.telegram_user_id


async def test_responsible_can_hand_off_ticket_but_not_change_chat(
    services: BotServices, ticket_id: int
) -> None:
    denied = await services.reassignment.options(
        ticket_id, CHAT, RESPONDER.telegram_user_id, is_admin=False
    )
    assert denied.outcome is ReassignOutcome.FORBIDDEN
    result = await services.reassignment.reassign(
        ticket_id, TICKET, COLLEAGUE.telegram_user_id, RESPONDER, is_admin=False
    )
    assert result.outcome is ReassignOutcome.OK
    # The new owner may hand it back; RESPONDER also stays the chat's responsible and
    # therefore keeps managing tickets of the chat.
    back = await services.reassignment.reassign(
        ticket_id, TICKET, RESPONDER.telegram_user_id, COLLEAGUE, is_admin=False
    )
    assert back.outcome is ReassignOutcome.OK
    again = await services.reassignment.reassign(
        ticket_id, TICKET, COLLEAGUE.telegram_user_id, RESPONDER, is_admin=False
    )
    assert again.outcome is ReassignOutcome.OK


async def test_stranger_is_forbidden(services: BotServices, ticket_id: int) -> None:
    for scope in (TICKET, CHAT):
        result = await services.reassignment.reassign(
            ticket_id, scope, COLLEAGUE.telegram_user_id, STRANGER, is_admin=False
        )
        assert result.outcome is ReassignOutcome.FORBIDDEN


async def test_rejects_non_responder_and_unchanged(services: BotServices, ticket_id: int) -> None:
    forged = await services.reassignment.reassign(
        ticket_id, TICKET, STRANGER.telegram_user_id, ADMIN, is_admin=True
    )
    assert forged.outcome is ReassignOutcome.INVALID_USER
    same = await services.reassignment.reassign(
        ticket_id, CHAT, RESPONDER.telegram_user_id, ADMIN, is_admin=True
    )
    assert same.outcome is ReassignOutcome.UNCHANGED


async def test_closed_ticket_cannot_be_reassigned(
    services: BotServices, ticket_id: int, clock: FakeClock
) -> None:
    await services.messages.process(incoming(11, RESPONDER, date=clock.now()))
    options = await services.reassignment.options(ticket_id, TICKET, ADMIN_ID, is_admin=True)
    assert options.outcome is ReassignOutcome.FINAL
    # The chat-level change is still possible from an old notification.
    chat_change = await services.reassignment.reassign(
        ticket_id, CHAT, COLLEAGUE.telegram_user_id, ADMIN, is_admin=True
    )
    assert chat_change.outcome is ReassignOutcome.OK


async def test_admin_changes_chat_responsible_for_new_messages(
    services: BotServices,
    ticket_id: int,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    result = await services.reassignment.reassign(
        ticket_id, CHAT, COLLEAGUE.telegram_user_id, ADMIN, is_admin=True
    )
    assert result.outcome is ReassignOutcome.OK
    await services.messages.process(incoming(12, user(3001), date=clock.now()))
    old, new = tickets(sync_factory)
    assert old.responsible_telegram_id_snapshot == RESPONDER.telegram_user_id  # snapshot kept
    assert new.responsible_telegram_id_snapshot == COLLEAGUE.telegram_user_id
    with sync_factory() as s:
        audit = s.scalars(
            select(ChatConfigurationAudit).where(
                ChatConfigurationAudit.action == ChatConfigAction.RESPONSIBLE_CHANGED.value
            )
        ).all()
    assert audit[-1].new_value is not None
    assert audit[-1].new_value["source"] == "notification"
    assert audit[-1].actor_telegram_user_id == ADMIN_ID


async def test_no_candidates(services: BotServices, clock: FakeClock) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    options = await services.reassignment.options(1, TICKET, ADMIN_ID, is_admin=True)
    assert options.outcome is ReassignOutcome.NO_CANDIDATES
