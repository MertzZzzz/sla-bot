from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import OutboxStatus, PendingReplyStatus
from app.db.models import OutboxEvent, PendingReply
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.services.notifications import DeliveryStatus, NotificationService
from app.services.sla import SlaService
from app.services.telegram_sender import PermanentDeliveryError, TransientDeliveryError
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.helpers import (
    ADMIN_ID,
    CLIENT,
    NOTIFY_CHAT,
    RESPONDER,
    FakeSender,
    outbox,
    reply_event_types,
    setup_chat,
    tickets,
)

pytestmark = pytest.mark.integration

UowFactory = Callable[[], SyncUnitOfWork]


@pytest.fixture
def sla(sync_uow: UowFactory, clock: FakeClock, settings: Settings) -> SlaService:
    return SlaService(sync_uow, clock, settings.celery)


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def notifier(
    sync_uow: UowFactory, sender: FakeSender, clock: FakeClock, settings: Settings
) -> NotificationService:
    return NotificationService(sync_uow, sender, clock, settings.celery)


async def overdue_ticket(services: BotServices, clock: FakeClock, message_id: int = 10) -> None:
    await services.messages.process(incoming(message_id, CLIENT, date=clock.now()))
    clock.advance(seconds=901)


async def test_answer_before_deadline_produces_no_notification(
    services: BotServices, sla: SlaService, clock: FakeClock, sync_factory: sessionmaker[Session]
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    clock.advance(minutes=5)
    await services.messages.process(incoming(11, RESPONDER, date=clock.now()))
    clock.advance(hours=1)
    assert sla.escalate_due() == []
    assert outbox(sync_factory) == []


async def test_scan_ignores_tickets_before_deadline(
    services: BotServices, sla: SlaService, clock: FakeClock
) -> None:
    await setup_chat(services)
    await services.messages.process(incoming(10, CLIENT, date=clock.now()))
    clock.advance(seconds=899)
    assert sla.escalate_due() == []


async def test_overdue_ticket_yields_exactly_one_notification(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    assert sla.escalate_due() == []  # second scan finds nothing

    outcome = notifier.deliver(event_id)
    assert outcome.status is DeliveryStatus.SENT
    repeated = notifier.deliver(event_id)  # Celery redelivery of the same task
    assert repeated.status is DeliveryStatus.SKIPPED

    assert len(sender.sent) == 1
    sent = sender.sent[0]
    assert (sent["chat_id"], sent["thread_id"]) == (NOTIFY_CHAT, 5)
    assert f"tg://user?id={RESPONDER.telegram_user_id}" in str(sent["text"])
    [ticket] = tickets(sync_factory)
    assert ticket.status is PendingReplyStatus.OVERDUE
    assert ticket.notification_message_id == outcome.message_id
    assert ticket.notification_chat_id == NOTIFY_CHAT
    assert ticket.notification_sent_at == clock.now()
    [event] = outbox(sync_factory)
    assert event.status is OutboxStatus.SENT
    assert event.attempts == 1
    assert reply_event_types(sync_factory, ticket.id) == ["created", "overdue"]


async def test_concurrent_scanners_create_single_event_per_ticket(
    services: BotServices,
    sync_uow: UowFactory,
    clock: FakeClock,
    settings: Settings,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    for i in range(30):
        await services.messages.process(incoming(10 + i, user(5000 + i), date=clock.now()))
    clock.advance(hours=1)
    small_batches = settings.celery.model_copy(update={"scan_batch_size": 7})
    scanners = [SlaService(sync_uow, clock, small_batches) for _ in range(4)]

    def run(scanner: SlaService) -> list[int]:
        created: list[int] = []
        while batch := scanner.escalate_due():
            created.extend(batch)
        return created

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, scanners))
    all_ids = [i for r in results for i in r]
    assert len(all_ids) == len(set(all_ids)) == 30
    assert len(outbox(sync_factory)) == 30
    assert all(t.status is PendingReplyStatus.OVERDUE for t in tickets(sync_factory))


async def test_two_workers_deliver_same_event_once(
    services: BotServices,
    sla: SlaService,
    sync_uow: UowFactory,
    clock: FakeClock,
    settings: Settings,
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    gate = threading.Event()
    sender = FakeSender(gate=gate)
    workers = [NotificationService(sync_uow, sender, clock, settings.celery) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(w.deliver, event_id) for w in workers]
        gate.set()
        outcomes = sorted(f.result().status.value for f in futures)
    assert outcomes == ["sent", "skipped"]
    assert len(sender.sent) == 1


async def test_responder_holding_lock_wins_over_scanner(
    services: BotServices,
    sla: SlaService,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    """Responder transaction locks the ticket while the scanner runs: scanner skips it."""
    await setup_chat(services)
    await overdue_ticket(services, clock)
    with sync_factory() as responder_tx:
        ticket = responder_tx.scalars(select(PendingReply).with_for_update()).one()
        assert sla.escalate_due() == []  # row is locked -> skipped, not blocked
        ticket.status = PendingReplyStatus.ANSWERED
        ticket.responded_at = clock.now()
        responder_tx.commit()
    assert sla.escalate_due() == []
    assert outbox(sync_factory) == []


async def test_answer_between_escalation_and_delivery_cancels_notification(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    await services.messages.process(incoming(20, RESPONDER, date=clock.now()))
    outcome = notifier.deliver(event_id)
    assert outcome.status is DeliveryStatus.CANCELLED
    assert sender.sent == []
    [event] = outbox(sync_factory)
    assert event.status is OutboxStatus.CANCELLED
    [ticket] = tickets(sync_factory)
    # Still counted as an SLA breach: answered after the deadline.
    assert ticket.status is PendingReplyStatus.ANSWERED
    assert ticket.overdue_at is not None


async def test_answer_after_notification_keeps_notification(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    sent = notifier.deliver(event_id)
    clock.advance(minutes=10)
    result = await services.messages.process(incoming(30, RESPONDER, date=clock.now()))
    assert result.action == "answered"
    [ticket] = tickets(sync_factory)
    assert ticket.status is PendingReplyStatus.ANSWERED
    assert ticket.responded_at == clock.now()
    assert ticket.notification_message_id == sent.message_id
    assert reply_event_types(sync_factory, ticket.id) == ["created", "overdue", "answered"]


async def test_transient_error_is_retried_with_backoff(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    sender.errors.append(TransientDeliveryError("timeout", error_type="TelegramNetworkError"))
    outcome = notifier.deliver(event_id)
    assert outcome.status is DeliveryStatus.RETRY
    assert outcome.retry_in_seconds == 5
    [event] = outbox(sync_factory)
    assert (event.status, event.attempts) == (OutboxStatus.PENDING, 1)
    assert "TelegramNetworkError" in (event.last_error or "")
    assert sla.deliverable_event_ids() == []  # backoff not elapsed yet
    assert notifier.deliver(event_id).status is DeliveryStatus.SKIPPED

    clock.advance(seconds=5)
    assert sla.deliverable_event_ids() == [event_id]
    assert notifier.deliver(event_id).status is DeliveryStatus.SENT
    assert len(sender.sent) == 1


async def test_retries_are_bounded(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    sender.errors.extend(TransientDeliveryError("x", error_type="Net") for _ in range(3))
    statuses = []
    for _ in range(3):
        statuses.append(notifier.deliver(event_id).status)
        clock.advance(hours=1)
    assert statuses == [DeliveryStatus.RETRY, DeliveryStatus.RETRY, DeliveryStatus.FAILED]
    assert outbox(sync_factory)[0].status is OutboxStatus.FAILED


async def test_permanent_error_marks_failed(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    sender.errors.append(
        PermanentDeliveryError("Forbidden: bot was kicked", error_type="TelegramForbiddenError")
    )
    assert notifier.deliver(event_id).status is DeliveryStatus.FAILED
    [event] = outbox(sync_factory)
    assert event.status is OutboxStatus.FAILED
    assert "TelegramForbiddenError" in (event.last_error or "")
    assert any(getattr(r, "event", None) == "notification_failed" for r in caplog.records)


async def test_missing_notification_chat_fails_without_sending(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await services.chats.add_chat(CHAT_ID, "Чат", "supergroup", ADMIN_ID)  # type: ignore[arg-type]
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()
    assert notifier.deliver(event_id).status is DeliveryStatus.FAILED
    assert sender.sent == []
    assert "not configured" in (outbox(sync_factory)[0].last_error or "")


async def test_crash_after_send_does_not_duplicate(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram accepted the message but the worker died before recording it."""
    await setup_chat(services)
    await overdue_ticket(services, clock)
    [event_id] = sla.escalate_due()

    def crash(*_: object, **__: object) -> None:
        raise SystemExit("worker killed")

    monkeypatch.setattr(notifier, "_record_sent", crash)
    with pytest.raises(SystemExit):
        notifier.deliver(event_id)
    monkeypatch.undo()
    assert len(sender.sent) == 1
    assert outbox(sync_factory)[0].status is OutboxStatus.PROCESSING

    # Task redelivered (acks_late) while the event is still "processing": no resend.
    assert notifier.deliver(event_id).status is DeliveryStatus.SKIPPED
    clock.advance(seconds=301)
    assert sla.recover_stale_processing() == []  # default policy: fail, do not resend
    [event] = outbox(sync_factory)
    assert event.status is OutboxStatus.FAILED
    assert "unknown" in (event.last_error or "")
    assert notifier.deliver(event_id).status is DeliveryStatus.SKIPPED
    assert len(sender.sent) == 1


async def test_stale_processing_can_be_resent_when_configured(
    services: BotServices,
    sync_uow: UowFactory,
    clock: FakeClock,
    settings: Settings,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    cfg = settings.celery.model_copy(update={"resend_stale_notifications": True})
    sla = SlaService(sync_uow, clock, cfg)
    [event_id] = sla.escalate_due()
    with sync_factory() as s:
        event = s.get(OutboxEvent, event_id)
        assert event is not None
        event.status = OutboxStatus.PROCESSING
        event.locked_at = clock.now()
        s.commit()
    clock.advance(seconds=301)
    assert sla.recover_stale_processing() == [event_id]
    assert outbox(sync_factory)[0].status is OutboxStatus.PENDING


async def test_notification_target_follows_current_settings(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
) -> None:
    await setup_chat(services)
    await overdue_ticket(services, clock)
    await services.chats.update(
        CHAT_ID,
        MonitoredChatUpdate(notification_chat_id=-100888, notification_thread_id=None),
        ADMIN_ID,
    )
    [event_id] = sla.escalate_due()
    notifier.deliver(event_id)
    assert (sender.sent[0]["chat_id"], sender.sent[0]["thread_id"]) == (-100888, None)
