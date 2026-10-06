"""'SLA is running out' warnings at 50% and 75% of the SLA, then the breach at 100%."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import OutboxEventType, OutboxStatus
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.services.notifications import DeliveryStatus, NotificationService
from app.services.sla import SlaService
from app.services.telegram_sender import PermanentDeliveryError
from tests.factories import CHAT_ID, FakeClock, incoming
from tests.integration.helpers import (
    ADMIN_ID,
    CLIENT,
    RESPONDER,
    FakeSender,
    outbox,
    setup_chat,
    tickets,
)

pytestmark = pytest.mark.integration
UowFactory = Callable[[], SyncUnitOfWork]


@pytest.fixture
def sla(sync_uow: UowFactory, clock: FakeClock, settings: Settings) -> SlaService:
    return SlaService(sync_uow, clock, settings.celery, (50, 75))


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def notifier(
    sync_uow: UowFactory, sender: FakeSender, clock: FakeClock, settings: Settings
) -> NotificationService:
    return NotificationService(sync_uow, sender, clock, settings.celery, lambda: [ADMIN_ID])


async def ticket(services: BotServices, clock: FakeClock, sla_seconds: int = 600) -> None:
    await setup_chat(services, sla_seconds=sla_seconds)
    await services.messages.process(incoming(10, CLIENT, text="Где счёт?", date=clock.now()))


def deliver_all(sla: SlaService, notifier: NotificationService, ids: list[int]) -> None:
    for event_id in ids:
        assert notifier.deliver(event_id).status is DeliveryStatus.SENT


async def test_warnings_at_half_and_three_quarters_then_breach(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=299)
    assert sla.escalate_warnings() == []

    clock.advance(seconds=1)  # 50% of 10 minutes
    half = sla.escalate_warnings()
    assert len(half) == 1
    assert sla.escalate_warnings() == []  # once per threshold
    deliver_all(sla, notifier, half)
    text = str(sender.sent[0]["text"])
    assert text.startswith("🟡 <b>SLA: прошла половина времени</b>")
    assert "Осталось: 5 мин (до " in text
    assert "Текст: Где счёт?" in text
    assert (sender.sent[0]["chat_id"], sender.sent[0]["thread_id"]) == (-100777, 5)

    clock.advance(seconds=150)  # 75%
    three_quarters = sla.escalate_warnings()
    deliver_all(sla, notifier, three_quarters)
    assert str(sender.sent[1]["text"]).startswith(
        "🟠 <b>SLA скоро истечёт</b> — прошло 75% времени"
    )
    assert "Осталось: 2 мин 30 сек" in str(sender.sent[1]["text"])

    clock.advance(seconds=151)  # past the deadline
    assert sla.escalate_warnings() == []
    deliver_all(sla, notifier, sla.escalate_due())
    assert str(sender.sent[2]["text"]).startswith("🔴 <b>SLA нарушен</b>")

    events = [(e.event_type, e.dedup_key) for e in outbox(sync_factory)]
    assert events == [
        (OutboxEventType.SLA_WARNING, "50"),
        (OutboxEventType.SLA_WARNING, "75"),
        (OutboxEventType.SLA_OVERDUE_NOTIFICATION, ""),
    ]
    [t] = tickets(sync_factory)
    assert t.warning_level == 75
    # Only the breach notification is remembered on the ticket (buttons/reassignment).
    assert t.notification_message_id == 503


async def test_only_the_highest_reached_threshold_is_sent(
    services: BotServices, sla: SlaService, clock: FakeClock, sync_factory: sessionmaker[Session]
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=480)  # 80%: both thresholds crossed at once
    sla.escalate_warnings()
    assert [e.dedup_key for e in outbox(sync_factory)] == ["75"]
    clock.advance(seconds=60)
    assert sla.escalate_warnings() == []


async def test_answer_stops_warnings(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
    sync_factory: sessionmaker[Session],
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=300)
    [event_id] = sla.escalate_warnings()
    await services.messages.process(incoming(11, RESPONDER, date=clock.now()))
    assert notifier.deliver(event_id).status is DeliveryStatus.CANCELLED  # answered meanwhile
    clock.advance(seconds=200)
    assert sla.escalate_warnings() == []
    assert sender.sent == []


async def test_no_warning_once_breached(
    services: BotServices, sla: SlaService, clock: FakeClock
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=700)  # scanner was down until after the deadline
    assert sla.escalate_warnings() == []
    assert len(sla.escalate_due()) == 1


async def test_warnings_can_be_disabled(
    services: BotServices, sync_uow: UowFactory, clock: FakeClock, settings: Settings
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=500)
    assert SlaService(sync_uow, clock, settings.celery, ()).escalate_warnings() == []


async def test_failed_warning_does_not_alert_admins(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=300)
    [event_id] = sla.escalate_warnings()
    sender.errors.append(PermanentDeliveryError("chat not found", error_type="TelegramBadRequest"))
    assert notifier.deliver(event_id).status is DeliveryStatus.FAILED
    assert sender.texts == []  # alerts are reserved for lost breach notifications


async def test_warning_goes_to_current_target(
    services: BotServices,
    sla: SlaService,
    notifier: NotificationService,
    sender: FakeSender,
    clock: FakeClock,
) -> None:
    await ticket(services, clock)
    clock.advance(seconds=300)
    [event_id] = sla.escalate_warnings()
    await services.chats.update(
        CHAT_ID,
        MonitoredChatUpdate(notification_chat_id=-100888, notification_thread_id=None),
        ADMIN_ID,
    )
    notifier.deliver(event_id)
    assert (sender.sent[0]["chat_id"], sender.sent[0]["thread_id"]) == (-100888, None)


async def test_concurrent_scanners_send_each_warning_once(
    services: BotServices,
    sync_uow: UowFactory,
    clock: FakeClock,
    settings: Settings,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_chat(services, sla_seconds=600)
    for i in range(20):
        await services.messages.process(
            incoming(
                10 + i, CLIENT.model_copy(update={"telegram_user_id": 5000 + i}), date=clock.now()
            )
        )
    clock.advance(seconds=300)
    small = settings.celery.model_copy(update={"scan_batch_size": 3})
    scanners = [SlaService(sync_uow, clock, small, (50, 75)) for _ in range(4)]

    def run(scanner: SlaService) -> list[int]:
        out: list[int] = []
        while batch := scanner.escalate_warnings():
            out.extend(batch)
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = [i for batch in pool.map(run, scanners) for i in batch]
    assert len(ids) == len(set(ids)) == 20
    assert all(e.status is OutboxStatus.PENDING for e in outbox(sync_factory))
