"""Pilot participants list and inviting them into customer chats."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import CreateChatInviteLink, DeleteMessage, SendMessage
from aiogram.types import Chat, SharedUser, User, UsersShared
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.bot.routers.customer_chat import INVITE_HINT
from app.bot.services import BotServices
from app.bot.texts import NOT_ADMIN_PRIVATE, PILOT_WELCOME
from app.core.config import Settings
from app.core.enums import ChatConfigAction, ChatType
from app.db.models import ChatConfigurationAudit
from app.schemas.users import TelegramUserData
from tests.factories import CHAT_ID
from tests.integration.bot_harness import RecordingSession, feed, message, reset_fsm
from tests.integration.helpers import ADMIN_ID
from tests.integration.test_settings_menu import Nav

pytestmark = pytest.mark.integration

ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="boss")
PRIVATE = Chat(id=ADMIN_ID, type="private")
GROUP = Chat(id=CHAT_ID, type="supergroup", title="ООО Заказчик")


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


async def add_people(nav: Nav, text: str | None = None, **kwargs: Any) -> None:
    await nav.send("/menu")
    await nav.press("Участники пилота")
    await nav.press("Добавить участников")
    await nav.send(text, **kwargs)


async def test_add_list_and_remove_participants(nav: Nav, services: BotServices) -> None:
    await services.users.upsert(
        TelegramUserData(telegram_user_id=4001, first_name="Ivan", username="Ivan")
    )
    await nav.send("/menu")
    await nav.press("Участники пилота")
    assert "Список пуст" in nav.text

    await nav.press("Добавить участников")
    await nav.send("@ivan, @olga\n123456789")
    assert "✅ Добавлены: @Ivan, @olga, 123456789" in nav.text
    assert {"@Ivan", "❔ @olga", "123456789"} <= set(nav.buttons())
    people = {p.label: p.telegram_user_id for p in await services.pilot.list_all()}
    assert people == {"@Ivan": 4001, "@olga": None, "123456789": 123456789}

    await nav.press("Добавить участников")
    await nav.send("@OLGA")
    assert "ℹ️ Уже в списке: @OLGA" in nav.text

    await nav.press("Добавить участников")
    await nav.send("@okname bad/name")
    assert "Не похоже на @username или ID: bad/name" in nav.text  # error, still waiting
    await nav.send("/cancel")

    await nav.send("/menu")
    await nav.press("Участники пилота")
    await nav.press("@olga")
    assert "Telegram ID: <i>неизвестен</i>" in nav.text
    assert "Кто добавил: Admin" in nav.text
    await nav.press("Удалить из списка")
    await nav.press("Да, удалить")
    assert [p.label for p in await services.pilot.list_all()] == ["@Ivan", "123456789"]


async def test_add_with_native_picker(nav: Nav, services: BotServices) -> None:
    shared = UsersShared(
        request_id=1,
        users=[
            SharedUser(user_id=5001, first_name="Anna", username="anna"),
            SharedUser(user_id=5002, first_name="Boris"),
        ],
    )
    await add_people(nav, None, users_shared=shared)
    labels = {p.label: p.telegram_user_id for p in await services.pilot.list_all()}
    assert labels == {"@anna": 5001, "Boris": 5002}


async def test_start_links_username_to_id(
    nav: Nav, bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await add_people(nav, "@olga")
    olga = User(id=7007, is_bot=False, first_name="Olga", username="olga")
    person = Nav(bot, tg, settings, services, Chat(id=7007, type="private"), olga)
    await person.send("/start")
    assert person.text == PILOT_WELCOME
    [participant] = await services.pilot.list_all()
    assert (participant.telegram_user_id, participant.display_name) == (7007, "Olga")
    stranger = Nav(
        bot,
        tg,
        settings,
        services,
        Chat(id=7008, type="private"),
        User(id=7008, is_bot=False, first_name="X"),
    )
    await stranger.send("/start")
    assert stranger.text == NOT_ADMIN_PRIVATE


async def setup_invite_scenario(services: BotServices, tg: RecordingSession) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    people: list[TelegramUserData | str] = [
        TelegramUserData(telegram_user_id=8001, first_name="Ivan", username="ivan"),  # started bot
        TelegramUserData(telegram_user_id=8002, first_name="Olga", username="olga"),  # DM blocked
        TelegramUserData(telegram_user_id=8003, first_name="Member", username="member"),
        "petr",  # unknown username
    ]
    await services.pilot.add(people, ADMIN_ID)
    tg.forbidden_chats.add(8002)
    tg.members[8003] = User(id=8003, is_bot=False, first_name="Member")


async def test_invite_from_chat_card(
    nav: Nav,
    tg: RecordingSession,
    services: BotServices,
    sync_factory: sessionmaker[Session],
) -> None:
    await setup_invite_scenario(services, tg)
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")
    await nav.press("Пригласить участников пилота")
    assert "Участники пилота (4): @ivan, @olga, @member, @petr" in nav.text
    await nav.press("📨 Пригласить")

    links = tg.of(CreateChatInviteLink)
    assert len(links) == 3  # member is skipped
    assert all(link.member_limit == 1 and link.chat_id == CHAT_ID for link in links)
    expire = links[0].expire_date
    assert expire is not None
    assert {link.name for link in links} == {"Пилот @ivan", "Пилот @olga", "Пилот @petr"}

    dms = [m for m in tg.of(SendMessage) if m.chat_id in (8001, 8002, 8003)]
    assert [m.chat_id for m in dms] == [8001, 8002]  # olga's attempt failed
    assert "Вас приглашают в чат «ООО Заказчик»" in dms[0].text
    assert "https://t.me/+invite" in dms[0].text

    report = nav.text
    assert "Отправлено в личку: 1" in report
    assert "Уже в чате: 1" in report
    assert "Переслать вручную: 2" in report
    assert "@olga — https://t.me/+invite" in report
    assert "(не нажимал /start у бота)" in report
    assert "@petr — https://t.me/+invite" in report
    assert "(бот не знает этого пользователя" in report

    with sync_factory() as s:
        audit = s.scalars(
            select(ChatConfigurationAudit).where(
                ChatConfigurationAudit.action == ChatConfigAction.PILOT_INVITED.value
            )
        ).one()
    assert audit.new_value == {"sent": 1, "member": 1, "manual": 2, "error": None}


async def test_invite_without_rights(nav: Nav, tg: RecordingSession, services: BotServices) -> None:
    await setup_invite_scenario(services, tg)
    tg.invites_forbidden = True
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")
    await nav.press("Пригласить участников пилота")
    await nav.press("📨 Пригласить")
    assert "Сделайте бота администратором" in nav.text
    assert [m for m in tg.of(SendMessage) if m.chat_id in (8001, 8002)] == []


async def test_invite_with_empty_list(nav: Nav, services: BotServices) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    await nav.send("/menu")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")
    await nav.press("Пригласить участников пилота")
    assert "Список участников пилота пуст" in nav.text
    assert "🧪 Участники пилота" in nav.buttons()


async def test_invite_all_in_customer_chat(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await setup_invite_scenario(services, tg)
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/invite_all"))
    assert tg.of(DeleteMessage)[0].chat_id == CHAT_ID
    assert all(m.chat_id != CHAT_ID for m in tg.of(SendMessage))  # nothing in the group
    report = [m for m in tg.of(SendMessage) if m.chat_id == ADMIN_ID]
    assert "Приглашения в «ООО Заказчик»" in report[0].text


async def test_invite_all_hint_when_admin_unreachable(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await setup_invite_scenario(services, tg)
    tg.forbidden_chats.add(ADMIN_ID)
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/invite_all"))
    group_messages = [m for m in tg.of(SendMessage) if m.chat_id == CHAT_ID]
    assert [m.text for m in group_messages] == [INVITE_HINT]  # no links leaked


async def test_invite_all_requires_monitored_chat_and_admin(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/invite_all"))
    assert "сначала выполните в нём /chat_add" in tg.of(SendMessage)[-1].text
    sent = len(tg.requests)
    customer = User(id=3000, is_bot=False, first_name="Customer")
    await feed(bot, settings, services, message(GROUP, customer, "/invite_all"))
    assert tg.of(CreateChatInviteLink) == []
    assert all(not isinstance(r, SendMessage) for r in tg.requests[sent:])


def test_invite_ttl() -> None:
    from app.bot.invites import INVITE_TTL

    assert timedelta(days=7) == INVITE_TTL
