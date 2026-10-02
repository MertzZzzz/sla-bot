"""Regression: a group ID entered without the minus sign silently broke notifications."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from aiogram import Bot
from sqlalchemy.orm import Session, sessionmaker

from app.bot.chat_target import candidates
from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType, OutboxStatus
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.services.notifications import DeliveryStatus, NotificationService
from app.services.sla import SlaService
from app.services.telegram_sender import PermanentDeliveryError
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.bot_harness import RecordingSession, reset_fsm, visible_chat
from tests.integration.helpers import ADMIN_ID, CLIENT, FakeSender, outbox
from tests.integration.test_settings_menu import Nav

pytestmark = pytest.mark.integration


def test_candidates() -> None:
    assert candidates(-5424221725) == [-5424221725]
    assert candidates(5424221725) == [-5424221725, -1005424221725, 5424221725]


@pytest.fixture
def tg() -> RecordingSession:
    reset_fsm()
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


@pytest.fixture
def nav(bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices) -> Nav:
    return Nav(bot, tg, settings, services)


async def enter_target(nav: Nav, text: str) -> None:
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")
    await nav.press("Уведомления")
    await nav.press("Указать chat_id вручную")
    await nav.send(text)


@pytest.fixture
async def chat(services: BotServices) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)


async def target(services: BotServices) -> tuple[int | None, int | None]:
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    return details.chat.notification_chat_id, details.chat.notification_thread_id


@pytest.mark.parametrize("real_id", [-5424221725, -1005424221725])
async def test_positive_group_id_is_corrected(
    nav: Nav, tg: RecordingSession, services: BotServices, chat: None, real_id: int
) -> None:
    tg.chats[real_id] = visible_chat(real_id, "Эскалации")
    await enter_target(nav, "5424221725")
    assert await target(services) == (real_id, None)
    assert "✅ Чат уведомлений: «Эскалации»" in nav.text
    assert "бот подобрал правильный" in nav.text


async def test_unknown_chat_is_rejected(nav: Nav, services: BotServices, chat: None) -> None:
    await enter_target(nav, "5424221725")
    assert "Бот не видит чат <code>5424221725</code>" in nav.text
    assert await target(services) == (None, None)  # nothing saved, still waiting for input


async def test_topic_requires_forum(
    nav: Nav, tg: RecordingSession, services: BotServices, chat: None
) -> None:
    tg.chats[-5424221725] = visible_chat(-5424221725, "Эскалации")
    await enter_target(nav, "-5424221725 7")
    assert "нет топиков" in nav.text
    assert await target(services) == (None, None)


async def test_failed_delivery_alerts_admins_and_is_retried_after_fix(
    services: BotServices,
    chat: None,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
    settings: Settings,
) -> None:
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=5424221725), ADMIN_ID
    )
    await services.messages.process(
        incoming(10, CLIENT, text="Помогите", date=clock.now(), title="ООО Заказчик")
    )
    clock.advance(seconds=901)
    sla = SlaService(sync_uow, clock, settings.celery)
    sender = FakeSender(
        errors=[
            PermanentDeliveryError("Bad Request: chat not found", error_type="TelegramBadRequest")
        ]
    )
    notifier = NotificationService(sync_uow, sender, clock, settings.celery, lambda: [ADMIN_ID])
    [event_id] = sla.escalate_due()

    assert notifier.deliver(event_id).status is DeliveryStatus.FAILED
    alert, copy = sender.texts
    assert alert["chat_id"] == ADMIN_ID
    assert "Уведомление о нарушении SLA не доставлено" in str(alert["text"])
    assert "chat not found" in str(alert["text"])
    assert "🔴 <b>SLA нарушен</b>" in str(copy["text"])  # the breach itself is not lost
    card = await services.chats.get_card(1)
    assert card is not None
    assert card.notification_error is not None
    assert "chat not found" in card.notification_error

    # Admin fixes the target: the failed escalation is queued again and delivered there.
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=-5424221725), ADMIN_ID
    )
    assert outbox(sync_factory)[0].status is OutboxStatus.PENDING
    assert sla.deliverable_event_ids() == [event_id]
    assert notifier.deliver(event_id).status is DeliveryStatus.SENT
    assert sender.sent[0]["chat_id"] == -5424221725
    card = await services.chats.get_card(1)
    assert card is not None and card.notification_error is None


async def test_answered_ticket_is_not_requeued(
    services: BotServices,
    chat: None,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
    clock: FakeClock,
    settings: Settings,
) -> None:
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=5424221725), ADMIN_ID
    )
    await services.chats.set_responsible(CHAT_ID, user(2000), ADMIN_ID)
    await services.messages.process(incoming(10, CLIENT, date=clock.now(), title="ООО Заказчик"))
    clock.advance(seconds=901)
    sla = SlaService(sync_uow, clock, settings.celery)
    sender = FakeSender(
        errors=[PermanentDeliveryError("chat not found", error_type="TelegramBadRequest")]
    )
    [event_id] = sla.escalate_due()
    NotificationService(sync_uow, sender, clock, settings.celery).deliver(event_id)
    await services.messages.process(
        incoming(11, user(2000), date=clock.now(), title="ООО Заказчик")
    )
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=-5424221725), ADMIN_ID
    )
    assert outbox(sync_factory)[0].status is OutboxStatus.FAILED
