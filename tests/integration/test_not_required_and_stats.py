from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices, build_bot_services
from app.core.config import Settings
from app.core.enums import PendingReplyStatus, Priority
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.schemas.stats import StatsQuery
from app.services.pending_replies import NotRequiredOutcome
from app.services.sla import SlaService
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
STRANGER = user(9999, "Stranger")


async def overdue(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    clock: FakeClock,
    settings: Settings,
) -> int:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    clock.advance(seconds=901)
    SlaService(sync_uow, clock, settings.celery).escalate_due()
    return 1


async def test_admin_marks_not_required_with_audit(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
    settings: Settings,
) -> None:
    ticket_id = await overdue(services, sync_uow, clock, settings)
    result = await services.pending.mark_not_required(ticket_id, ADMIN, is_admin=True)
    assert result.outcome is NotRequiredOutcome.MARKED
    assert result.ticket is not None and result.ticket.status is PendingReplyStatus.NOT_REQUIRED
    [ticket] = tickets(sync_factory)
    assert ticket.not_required_by_telegram_id == ADMIN_ID
    assert ticket.not_required_at == clock.now()
    assert reply_event_types(sync_factory, ticket_id) == ["created", "overdue", "not_required"]

    again = await services.pending.mark_not_required(ticket_id, ADMIN, is_admin=True)
    assert again.outcome is NotRequiredOutcome.ALREADY_FINAL
    assert reply_event_types(sync_factory, ticket_id)[-1] == "not_required"
    # A late responder message does not reopen/close a not_required ticket.
    late = await services.messages.process(incoming(50, RESPONDER, date=clock.now()))
    assert late.action == "no_match"


async def test_simultaneous_presses_mark_once(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
    settings: Settings,
) -> None:
    ticket_id = await overdue(services, sync_uow, clock, settings)
    results = await asyncio.gather(
        services.pending.mark_not_required(ticket_id, ADMIN, is_admin=True),
        services.pending.mark_not_required(ticket_id, RESPONDER, is_admin=False),
    )
    outcomes = sorted(r.outcome.value for r in results)
    assert outcomes == ["already_final", "marked"]
    assert reply_event_types(sync_factory, ticket_id).count("not_required") == 1


async def test_permissions(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
    settings: Settings,
    async_factory: object,
) -> None:
    ticket_id = await overdue(services, sync_uow, clock, settings)
    denied = await services.pending.mark_not_required(ticket_id, STRANGER, is_admin=False)
    assert denied.outcome is NotRequiredOutcome.FORBIDDEN
    assert tickets(sync_factory)[0].status is PendingReplyStatus.OVERDUE

    strict = settings.model_copy(
        update={"app": settings.app.model_copy(update={"responsible_can_mark_not_required": False})}
    )
    strict_services = build_bot_services(strict, async_factory, clock)  # type: ignore[arg-type]
    denied = await strict_services.pending.mark_not_required(ticket_id, RESPONDER, is_admin=False)
    assert denied.outcome is NotRequiredOutcome.FORBIDDEN

    allowed = await services.pending.mark_not_required(ticket_id, RESPONDER, is_admin=False)
    assert allowed.outcome is NotRequiredOutcome.MARKED

    missing = await services.pending.mark_not_required(424242, ADMIN, is_admin=True)
    assert missing.outcome is NotRequiredOutcome.NOT_FOUND


async def test_stats_for_period(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    clock: FakeClock,
    settings: Settings,
) -> None:
    await setup_chat(services, sla_seconds=600)
    sla = SlaService(sync_uow, clock, settings.celery)
    start = clock.now()

    # Outside the period (40 days ago, answered after an hour): ignored by /stats 30.
    clock.current = start - timedelta(days=40)
    await services.messages.process(incoming(1, CLIENT, date=clock.now()))
    clock.advance(hours=1)
    await services.messages.process(incoming(2, RESPONDER, date=clock.now()))
    clock.current = start

    async def ticket(msg_id: int) -> None:
        await services.messages.process(incoming(msg_id, CLIENT, date=clock.now()))

    async def answer(msg_id: int) -> None:
        await services.messages.process(incoming(msg_id, RESPONDER, date=clock.now()))

    await ticket(10)  # answered in 2 min
    clock.advance(minutes=2)
    await answer(11)
    await ticket(12)  # answered in 4 min
    clock.advance(minutes=4)
    await answer(13)
    await ticket(14)  # answered late after 20 min (breach)
    clock.advance(minutes=20)
    sla.escalate_due()
    await answer(15)
    await ticket(16)  # overdue, still open
    await ticket(17)  # marked not required
    clock.advance(minutes=11)
    sla.escalate_due()
    await services.pending.mark_not_required(6, ADMIN, is_admin=True)
    await ticket(18)  # waiting, within SLA

    [row] = await services.stats.collect(StatsQuery(days=30))
    assert row.chat_title == "Поддержка VIP"
    assert row.priority is Priority.P3
    assert row.responsible_telegram_id == RESPONDER.telegram_user_id
    assert row.responsible_name == "Responder2000"
    assert row.created == 6
    assert row.answered == 3
    assert row.answered_within_sla == 2
    assert row.overdue == 2  # one answered late + one still open past deadline
    assert row.not_required == 1
    assert row.open_now == 2
    assert row.avg_response_seconds == pytest.approx((120 + 240 + 1200) / 3)
    assert row.median_response_seconds == pytest.approx(240)
    assert row.sla_compliance == pytest.approx(0.5)


async def test_stats_grouped_by_responsible_and_empty(
    services: BotServices, clock: FakeClock
) -> None:
    assert await services.stats.collect(StatsQuery(days=30)) == []
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    await services.chats.set_responsible(CHAT_ID, user(4000, "New"), ADMIN_ID)
    await services.chats.update(CHAT_ID, MonitoredChatUpdate(priority=Priority.P1), ADMIN_ID)
    await services.messages.process(incoming(11, CLIENT, date=clock.now()))
    rows = await services.stats.collect(StatsQuery(days=1))
    assert sorted((r.responsible_telegram_id, r.created) for r in rows) == [(2000, 1), (4000, 1)]
    assert all(r.priority is Priority.P1 for r in rows)
    assert all(r.sla_compliance is None for r in rows)
