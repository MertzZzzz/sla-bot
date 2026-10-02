"""New chats: admins are responders by default; notifications link to the chat."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from aiogram import Bot
from aiogram.methods import CreateChatInviteLink
from aiogram.types import Chat, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.bot.services import BotServices, build_bot_services
from app.core.config import Settings
from app.core.enums import ChatConfigAction, ChatType
from app.db.models import ChatConfigurationAudit
from app.db.uow import SyncUnitOfWork
from app.schemas.chats import MonitoredChatUpdate
from app.schemas.users import TelegramUserData
from app.services.notifications import NotificationService
from app.services.sla import SlaService
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.bot_harness import (
    RecordingSession,
    feed,
    message,
    reset_fsm,
    visible_chat,
)
from tests.integration.helpers import ADMIN_ID, CLIENT, FakeSender

pytestmark = pytest.mark.integration

ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="boss")
GROUP = Chat(id=CHAT_ID, type="supergroup", title="ООО Заказчик")


@pytest.fixture
def defaults_on(
    settings: Settings, async_factory: async_sessionmaker[AsyncSession], clock: FakeClock
) -> BotServices:
    on = settings.model_copy(
        update={"app": settings.app.model_copy(update={"admins_as_default_responders": True})}
    )
    return build_bot_services(on, async_factory, clock)


@pytest.fixture
def tg() -> RecordingSession:
    reset_fsm()
    return RecordingSession()


@pytest.fixture
def bot(tg: RecordingSession) -> Bot:
    return Bot("42:TEST", session=tg)


async def responder_ids(services: BotServices) -> set[int]:
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    return {u.telegram_user_id for u in details.responders}


async def test_new_chat_gets_all_admins_as_responders(
    bot: Bot,
    settings: Settings,
    defaults_on: BotServices,
    sync_factory: sessionmaker[Session],
) -> None:
    await defaults_on.admins.add(
        TelegramUserData(telegram_user_id=4242, first_name="Ivan"), ADMIN_ID
    )
    await defaults_on.admins.add(TelegramUserData(telegram_user_id=4343), ADMIN_ID)  # never wrote
    await feed(bot, settings, defaults_on, message(GROUP, ADMIN_TG, "/chat_add"))
    assert await responder_ids(defaults_on) == {ADMIN_ID, 4242, 4343}
    with sync_factory() as s:
        audit = s.scalars(
            select(ChatConfigurationAudit).where(
                ChatConfigurationAudit.action == ChatConfigAction.RESPONDER_ADDED.value
            )
        ).one()
    assert audit.new_value == {
        "telegram_user_ids": [ADMIN_ID, 4242, 4343],
        "source": "admins_by_default",
    }

    # Removable like any responder, and a repeated /chat_add does not bring them back.
    await defaults_on.chats.remove_responder(
        CHAT_ID, TelegramUserData(telegram_user_id=4343), ADMIN_ID
    )
    await feed(bot, settings, defaults_on, message(GROUP, ADMIN_TG, "/chat_add"))
    assert await responder_ids(defaults_on) == {ADMIN_ID, 4242}


async def test_admins_messages_count_as_answers(defaults_on: BotServices, clock: FakeClock) -> None:
    await defaults_on.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    await defaults_on.messages.process(incoming(10, CLIENT, date=clock.now(), title="ООО Заказчик"))
    admin_msg = incoming(11, user(ADMIN_ID, "Admin"), date=clock.now(), title="ООО Заказчик")
    assert (await defaults_on.messages.process(admin_msg)).action == "answered"


async def test_default_can_be_disabled(services: BotServices) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    assert await responder_ids(services) == set()


async def test_public_chat_link(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    public = Chat(id=CHAT_ID, type="supergroup", title="ООО Заказчик", username="client_chat")
    await feed(bot, settings, services, message(public, ADMIN_TG, "/chat_add"))
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    assert details.chat.chat_link == "https://t.me/client_chat"
    assert tg.of(CreateChatInviteLink) == []


async def test_private_chat_gets_join_request_invite_link(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    tg.chats[CHAT_ID] = visible_chat(CHAT_ID, "ООО Заказчик")
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))
    [invite] = tg.of(CreateChatInviteLink)
    assert invite.creates_join_request is True
    assert invite.member_limit is None
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    assert details.chat.chat_link is not None
    assert details.chat.chat_link.startswith("https://t.me/+invite")


async def test_no_rights_means_no_link(
    bot: Bot, tg: RecordingSession, settings: Settings, services: BotServices
) -> None:
    tg.chats[CHAT_ID] = visible_chat(CHAT_ID, "ООО Заказчик")
    tg.invites_forbidden = True
    await feed(bot, settings, services, message(GROUP, ADMIN_TG, "/chat_add"))
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None and details.chat.chat_link is None


async def test_username_seen_later_updates_link(services: BotServices, clock: FakeClock) -> None:
    await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    msg = incoming(10, CLIENT, date=clock.now(), title="ООО Заказчик").model_copy(
        update={"chat_username": "became_public"}
    )
    await services.messages.process(msg)
    details = await services.chats.get_details(CHAT_ID)
    assert details is not None
    assert details.chat.chat_link == "https://t.me/became_public"


async def test_notification_links_to_chat(
    services: BotServices,
    sync_uow: Callable[[], SyncUnitOfWork],
    clock: FakeClock,
    settings: Settings,
) -> None:
    await services.chats.add_chat(
        CHAT_ID,
        "ООО Заказчик",
        ChatType.SUPERGROUP,
        ADMIN_ID,
        username="client_chat",
    )
    await services.chats.update(
        CHAT_ID, MonitoredChatUpdate(notification_chat_id=-100777), ADMIN_ID
    )
    await services.messages.process(incoming(10, CLIENT, date=clock.now(), title="ООО Заказчик"))
    clock.advance(seconds=901)
    sender = FakeSender()
    [event_id] = SlaService(sync_uow, clock, settings.celery).escalate_due()
    NotificationService(sync_uow, sender, clock, settings.celery).deliver(event_id)
    assert 'Чат: <a href="https://t.me/client_chat"><b>ООО Заказчик</b></a>' in str(
        sender.sent[0]["text"]
    )
