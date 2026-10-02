"""Regression: a group ID entered without the minus sign silently broke notifications."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Chat, ChatShared, ReplyKeyboardMarkup, User
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
from tests.integration.bot_harness import (
    RecordingSession,
    callback,
    feed,
    reset_fsm,
    visible_chat,
)
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


SERVICE_GROUP = Chat(id=-1005424221725, type="supergroup", title="Служебная", is_forum=True)


async def test_notify_here_in_any_group_and_topic(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, chat: None
) -> None:
    topic = Nav(
        bot, tg, settings, services, SERVICE_GROUP, message_thread_id=9, is_topic_message=True
    )
    await topic.send("/notify_here", message_thread_id=9, is_topic_message=True)
    assert "направлять в этот топик" in topic.text
    await topic.press("ООО Заказчик")
    assert await target(services) == (-1005424221725, 9)
    assert "будут приходить сюда (в этот топик)" in topic.text

    plain = Nav(bot, tg, settings, services, SERVICE_GROUP)
    await plain.send("/notify_here")
    assert "✓ ООО Заказчик" not in plain.buttons()  # target is the topic, not the group
    await plain.press("ООО Заказчик")
    assert await target(services) == (-1005424221725, None)


async def test_notify_here_admin_only(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices, chat: None
) -> None:
    admin = Nav(bot, tg, settings, services, SERVICE_GROUP)
    await admin.send("/notify_here")
    stranger = Nav(
        bot, tg, settings, services, SERVICE_GROUP, User(id=9999, is_bot=False, first_name="S")
    )
    sent = len(tg.requests)
    await stranger.send("/notify_here")
    assert all(not isinstance(r, SendMessage) for r in tg.requests[sent:])
    update = callback(SERVICE_GROUP, stranger.sender, admin.buttons()["ООО Заказчик"])
    await feed(bot, settings, services, update)
    assert tg.of(AnswerCallbackQuery)[-1].show_alert
    assert await target(services) == (None, None)


async def test_pick_group_with_native_picker(
    nav: Nav, tg: RecordingSession, services: BotServices, chat: None
) -> None:
    tg.chats[-1005424221725] = visible_chat(-1005424221725, "Служебная")
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")
    await nav.press("Уведомления")
    await nav.press("Выбрать группу")
    picker = [m for m in tg.of(SendMessage) if isinstance(m.reply_markup, ReplyKeyboardMarkup)]
    assert picker[-1].reply_markup.keyboard[0][0].request_chat.bot_is_member
    await nav.send(
        None, chat_shared=ChatShared(request_id=2, chat_id=-1005424221725, title="Служебная")
    )
    assert await target(services) == (-1005424221725, None)
    assert "✅ Чат уведомлений: «Служебная»" in nav.text
