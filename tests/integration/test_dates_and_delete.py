"""Reference pilot dates and deleting a chat from the menu."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from aiogram import Bot
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices
from app.core.config import Settings
from app.core.enums import ChatType
from app.db.models import ChatConfigurationAudit, OutboxEvent, PendingReply, ReplyEvent
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.services.sla import SlaService
from tests.factories import CHAT_ID, FakeClock, incoming
from tests.integration.bot_harness import RecordingSession, reset_fsm
from tests.integration.helpers import ADMIN_ID, CLIENT, count
from tests.integration.test_settings_menu import Nav

pytestmark = pytest.mark.integration


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


@pytest.fixture
async def chat(services: BotServices) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)


async def open_card(nav: Nav) -> None:
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")


async def pilot(services: BotServices) -> tuple[date | None, date | None]:
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    return details.chat.pilot_start, details.chat.pilot_end


async def test_set_and_clear_pilot_dates(nav: Nav, services: BotServices, chat: None) -> None:
    await open_card(nav)
    await nav.press("Сроки пилота")
    assert "Начало пилота: <i>не задано</i>" in nav.text
    await nav.press("✏️ Начало")
    await nav.send("01.11.2026")
    assert nav.text.startswith("✅ Начало пилота: 01.11.2026")
    await nav.press("✏️ Окончание")
    await nav.send("15.10.2026")
    assert "не может быть раньше начала" in nav.text  # rejected, still waiting
    await nav.send("30.11.26")
    assert await pilot(services) == (date(2026, 11, 1), date(2026, 11, 30))
    assert "Окончание пилота: 30.11.2026" in nav.text

    await nav.press("Очистить начало")
    assert await pilot(services) == (None, date(2026, 11, 30))
    await nav.press("✏️ Окончание")
    await nav.send("-")
    assert await pilot(services) == (None, None)

    await nav.press("К настройкам чата")
    assert "Начало пилота: <i>не задано</i>" in nav.text


async def test_dates_are_audited(
    services: BotServices, chat: None, sync_factory: sessionmaker[Session]
) -> None:
    before = count(sync_factory, ChatConfigurationAudit)
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(pilot_start=date(2026, 11, 1)), ADMIN_ID
    )
    assert count(sync_factory, ChatConfigurationAudit) == before + 1


async def test_delete_chat_with_history(
    nav: Nav,
    services: BotServices,
    chat: None,
    clock: FakeClock,
    settings: Settings,
    sync_uow: Callable[[], SyncUnitOfWork],
    sync_factory: sessionmaker[Session],
) -> None:
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=-100777), ADMIN_ID
    )
    await services.messages.process(incoming(10, CLIENT, date=clock.now(), title="ООО Заказчик"))
    clock.advance(seconds=901)
    SlaService(sync_uow, clock, settings.celery).escalate_due()
    assert count(sync_factory, OutboxEvent) == 1

    await open_card(nav)
    await nav.press("Удалить чат")
    assert "Удалить чат «ООО Заказчик»?" in nav.text
    assert "открытых сейчас: 1" in nav.text
    assert "⏸ Лучше выключить мониторинг" in nav.buttons()
    await nav.press("Да, удалить")
    assert "Чатов пока нет" in nav.text
    assert await services.chats.list_chats() == []
    for model in (PendingReply, ReplyEvent, OutboxEvent, ChatConfigurationAudit):
        assert count(sync_factory, model) == 0, model

    # Messages in the deleted chat are no longer tracked.
    result = await services.messages.process(incoming(11, CLIENT, date=clock.now()))
    assert result.action == "ignored"


async def test_delete_can_be_avoided_by_disabling(
    nav: Nav, services: BotServices, chat: None
) -> None:
    await open_card(nav)
    await nav.press("Удалить чат")
    await nav.press("Лучше выключить мониторинг")
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and not details.chat.is_enabled


async def test_buttons_of_deleted_chat_are_harmless(
    nav: Nav, services: BotServices, chat: None
) -> None:
    await open_card(nav)
    stale_card_buttons = nav.buttons()
    await nav.press("Удалить чат")
    await nav.press("Да, удалить")
    nav_screen = nav.screen
    # Press a button from the old card: the bot answers "not found" and shows the list.
    from tests.integration.bot_harness import callback, feed

    update = callback(nav.chat, nav.sender, stale_card_buttons["⏱ SLA"], text=nav_screen.text)
    await feed(nav.bot, nav.settings, nav.services, update)
    assert "Чатов пока нет" in nav.text
