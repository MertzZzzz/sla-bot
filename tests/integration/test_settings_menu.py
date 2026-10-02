"""Inline settings menu, driven like a user would: by pressing buttons by their text."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, ReplyKeyboardMarkup, SharedUser, User, UsersShared

from app.bot.services import BotServices
from app.bot.texts import NOT_ADMIN_PRIVATE
from app.core.config import Settings
from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.schemas.chats import MonitoredChatDetails
from app.schemas.users import TelegramUserData
from tests.factories import CHAT_ID, FakeClock, incoming, user
from tests.integration.bot_harness import (
    RecordingSession,
    buttons,
    callback,
    feed,
    message,
    reset_fsm,
    visible_chat,
)
from tests.integration.helpers import ADMIN_CHAT_ID, ADMIN_ID

pytestmark = pytest.mark.integration

ADMIN_TG = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="boss")
STRANGER = User(id=9999, is_bot=False, first_name="Stranger")
PRIVATE = Chat(id=ADMIN_ID, type="private")
ADMIN_CHAT = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Админы", is_forum=True)


class Nav:
    """Drives the bot like a person: send text, press buttons on the latest screen."""

    def __init__(
        self,
        bot: Bot,
        tg: RecordingSession,
        settings: Settings,
        services: BotServices,
        chat: Chat = PRIVATE,
        sender: User = ADMIN_TG,
        **message_kwargs: Any,
    ) -> None:
        self.bot, self.tg, self.settings, self.services = bot, tg, settings, services
        self.chat, self.sender, self.message_kwargs = chat, sender, message_kwargs

    @property
    def screen(self) -> Any:
        return self.tg.last_screen()

    @property
    def text(self) -> str:
        return str(self.screen.text)

    def buttons(self) -> dict[str, str]:
        return buttons(self.screen.reply_markup)

    async def send(self, text: str | None = None, **kwargs: Any) -> None:
        await feed(
            self.bot, self.settings, self.services, message(self.chat, self.sender, text, **kwargs)
        )

    async def press(self, label: str) -> None:
        options = self.buttons()
        matches = [data for text, data in options.items() if label in text]
        assert matches, f"no button {label!r} in {list(options)}"
        update = callback(
            self.chat,
            self.sender,
            matches[0],
            text=self.text,
            markup=self.screen.reply_markup,
            **self.message_kwargs,
        )
        await feed(self.bot, self.settings, self.services, update)


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
async def chat_id(services: BotServices) -> int:
    _, chat = await services.chats.add_chat(CHAT_ID, "ООО Заказчик", ChatType.SUPERGROUP, ADMIN_ID)
    return chat.id


async def details(services: BotServices) -> MonitoredChatDetails:
    result = await services.chats.get_details(CHAT_ID)
    assert result is not None
    return result


async def open_chat(nav: Nav) -> None:
    await nav.send("/start")
    await nav.press("Чаты")
    await nav.press("ООО Заказчик")


# --- access -------------------------------------------------------------------------


async def test_main_menu_sections(nav: Nav) -> None:
    await nav.send("/start")
    assert set(nav.buttons()) == {"👮 Администраторы", "💬 Чаты", "🧪 Участники пилота"}
    await nav.send("/menu")  # same menu
    assert set(nav.buttons()) == {"👮 Администраторы", "💬 Чаты", "🧪 Участники пилота"}


async def test_non_admin_is_refused(nav: Nav, tg: RecordingSession) -> None:
    stranger = Nav(nav.bot, tg, nav.settings, nav.services, Chat(id=9999, type="private"), STRANGER)
    await stranger.send("/start")
    assert stranger.text == NOT_ADMIN_PRIVATE
    await nav.send("/start")
    stranger.message_kwargs = {}
    update = callback(stranger.chat, STRANGER, nav.buttons()["💬 Чаты"])
    edits_before = len(tg.of(EditMessageText))
    await feed(nav.bot, nav.settings, nav.services, update)
    assert tg.of(EditMessageText)[edits_before:] == []
    assert tg.of(AnswerCallbackQuery)[-1].show_alert


async def test_menu_works_in_admin_chat_only(nav: Nav, tg: RecordingSession) -> None:
    admin_chat = Nav(nav.bot, tg, nav.settings, nav.services, ADMIN_CHAT)
    await admin_chat.send("/menu")
    assert "💬 Чаты" in admin_chat.buttons()
    sent = len(tg.of(SendMessage))
    other = Nav(nav.bot, tg, nav.settings, nav.services, Chat(id=-100123, type="supergroup"))
    await other.send("/menu")
    assert len(tg.of(SendMessage)) == sent


# --- chats --------------------------------------------------------------------------


async def test_empty_chat_list(nav: Nav) -> None:
    await nav.send("/start")
    await nav.press("Чаты")
    assert "Чатов пока нет" in nav.text


async def test_chat_card_shows_every_setting_including_empty(nav: Nav, chat_id: int) -> None:
    await open_chat(nav)
    text = nav.text
    for label in (
        "ООО Заказчик",
        f"ID чата: <code>{CHAT_ID}</code> (супергруппа)",
        "Мониторинг: ✅ включён",
        "Приоритет: P3",
        "SLA: 15 мин",
        "Режим ответа: Любое сообщение",
        "Часовой пояс: Europe/Moscow",
        "Ответственный: <i>не задано</i>",
        "Отвечающие (0): <i>не задано</i>",
        "Чат уведомлений: <i>не задано</i>",
        "Топик уведомлений: <i>не задано</i>",
        "Открытых ожиданий: 0",
        "Подключён:",
    ):
        assert label in text, label
    assert {
        "⏸ Выключить мониторинг",
        "🔺 Приоритет",
        "⏱ SLA",
        "🔀 Режим ответа",
        "🌍 Часовой пояс",
        "👤 Ответственный",
        "👥 Отвечающие",
        "🔔 Уведомления",
        "🕒 Открытые ожидания (0)",
        "📨 Пригласить участников пилота",
        "« Чаты",
    } == set(nav.buttons())


async def test_change_settings_with_buttons(nav: Nav, chat_id: int, services: BotServices) -> None:
    await open_chat(nav)
    await nav.press("Приоритет")
    await nav.press("P1")
    await nav.press("SLA")
    assert "✓ 15 мин" in nav.buttons()
    await nav.press("30 мин")
    await nav.press("Режим ответа")
    await nav.press("Только Reply")
    await nav.press("Часовой пояс")
    await nav.press("Asia/Yekaterinburg")
    result = (await details(services)).chat
    assert result.priority is Priority.P1
    assert result.sla_seconds == 1800
    assert result.reply_match_mode is ReplyMatchMode.REPLY_ONLY
    assert result.timezone == "Asia/Yekaterinburg"
    assert "Приоритет: P1" in nav.text  # back on the card after each change

    await nav.press("Выключить мониторинг")
    assert not (await details(services)).chat.is_enabled
    assert "▶️ Включить мониторинг" in nav.buttons()
    await nav.press("Включить мониторинг")
    assert (await details(services)).chat.is_enabled


async def test_manual_sla_and_timezone_input(nav: Nav, chat_id: int, services: BotServices) -> None:
    await open_chat(nav)
    await nav.press("SLA")
    await nav.press("Ввести вручную")
    await nav.send("через часок")
    assert "Введите длительность" in nav.text  # error, still waiting for input
    await nav.send("1ч 30м")
    assert (await details(services)).chat.sla_seconds == 5400
    assert nav.text.startswith("✅ SLA: 1 ч 30 мин")
    await nav.press("Часовой пояс")
    await nav.press("Другой")
    await nav.send("Europe/Berlin")
    assert (await details(services)).chat.timezone == "Europe/Berlin"


async def test_cancel_manual_input(nav: Nav, chat_id: int, services: BotServices) -> None:
    await open_chat(nav)
    await nav.press("SLA")
    await nav.press("Ввести вручную")
    await nav.press("Отмена")
    assert "⏱ <b>SLA</b>" in nav.text  # back on the SLA screen
    await nav.send("45")  # not treated as input any more
    assert (await details(services)).chat.sla_seconds == 900
    await nav.press("Ввести вручную")
    await nav.send("/cancel")
    await nav.send("45")
    assert (await details(services)).chat.sla_seconds == 900


async def test_people_are_picked_from_chat_participants(
    nav: Nav, chat_id: int, services: BotServices, clock: FakeClock
) -> None:
    await services.messages.process(
        incoming(10, user(3000, "Client"), date=clock.now(), title="ООО Заказчик")
    )
    await services.messages.process(
        incoming(11, user(2000, "Manager"), date=clock.now(), title="ООО Заказчик")
    )
    await open_chat(nav)
    await nav.press("👤 Ответственный")
    assert "Текущий: <i>не задано</i>" in nav.text
    await nav.press("Manager2000")
    assert (await details(services)).responsible.telegram_user_id == 2000  # type: ignore[union-attr]
    assert "Ответственный: Manager2000" in nav.text

    await nav.press("👥 Отвечающие")
    assert "✅ Manager2000" in nav.buttons()  # auto-added as responsible
    await nav.press("➕ Client3000")
    assert {u.telegram_user_id for u in (await details(services)).responders} == {2000, 3000}
    await nav.press("✅ Client3000")
    assert {u.telegram_user_id for u in (await details(services)).responders} == {2000}

    await nav.press("К настройкам чата")
    await nav.press("👤 Ответственный")
    await nav.press("Снять ответственного")
    assert (await details(services)).responsible is None


async def test_manual_user_input(
    nav: Nav, chat_id: int, services: BotServices, tg: RecordingSession
) -> None:
    tg.members[5555] = User(id=5555, is_bot=False, first_name="Pavel", username="pavel")
    await open_chat(nav)
    await nav.press("👤 Ответственный")
    await nav.press("Указать вручную")
    assert any(isinstance(m.reply_markup, ReplyKeyboardMarkup) for m in tg.of(SendMessage))
    await nav.send("@nobody_knows")
    assert "пока неизвестен боту" in nav.text
    await nav.send("5555")  # resolved through getChatMember
    responsible = (await details(services)).responsible
    assert responsible is not None
    assert (responsible.telegram_user_id, responsible.username) == (5555, "pavel")

    await nav.press("👥 Отвечающие")
    await nav.press("Добавить вручную")
    shared = UsersShared(
        request_id=1, users=[SharedUser(user_id=6666, first_name="Olga", username="olga")]
    )
    await nav.send(None, users_shared=shared)  # Telegram's native user picker
    assert 6666 in {u.telegram_user_id for u in (await details(services)).responders}
    await nav.press("👥 Отвечающие")
    await nav.press("Добавить вручную")
    await nav.send("@olga")  # now known to the bot
    assert "уже среди отвечающих" in nav.text


async def test_notifications_here_in_admin_topic(
    nav: Nav, chat_id: int, services: BotServices, tg: RecordingSession
) -> None:
    topic = Nav(
        nav.bot,
        tg,
        nav.settings,
        nav.services,
        ADMIN_CHAT,
        message_thread_id=7,
        is_topic_message=True,
    )
    await topic.send("/menu", message_thread_id=7, is_topic_message=True)
    await topic.press("Чаты")
    await topic.press("ООО Заказчик")
    await topic.press("Уведомления")
    await topic.press("Отправлять сюда")
    chat = (await details(services)).chat
    assert (chat.notification_chat_id, chat.notification_thread_id) == (ADMIN_CHAT_ID, 7)
    assert f"Чат уведомлений: <code>{ADMIN_CHAT_ID}</code>" in topic.text

    await topic.press("Уведомления")
    await topic.press("Указать chat_id вручную")
    tg.chats[-100999] = visible_chat(-100999, "Эскалации", forum=True)
    await topic.send("-100999 3")
    chat = (await details(services)).chat
    assert (chat.notification_chat_id, chat.notification_thread_id) == (-100999, 3)
    await topic.press("Уведомления")
    await topic.press("Отключить уведомления")
    assert (await details(services)).chat.notification_chat_id is None


async def test_open_tickets_screen(
    nav: Nav, chat_id: int, services: BotServices, clock: FakeClock
) -> None:
    await services.messages.process(
        incoming(10, user(3000, "Client"), text="Где счёт?", date=clock.now(), title="ООО Заказчик")
    )
    await open_chat(nav)
    await nav.press("Открытые ожидания (1)")
    assert "Client3000: Где счёт?" in nav.text


# --- administrators ---------------------------------------------------------------------


async def test_admins_section(nav: Nav, services: BotServices, tg: RecordingSession) -> None:
    await services.users.upsert(
        TelegramUserData(telegram_user_id=1000, first_name="Admin", username="boss")
    )
    await services.users.upsert(
        TelegramUserData(telegram_user_id=4242, first_name="Ivan", username="ivan")
    )
    await services.admins.sync_env_admins()
    await nav.send("/start")
    await nav.press("Администраторы")
    assert "🔒 @boss" in nav.buttons()

    await nav.press("Добавить администратора")
    await nav.send("@ivan")
    assert nav.text.startswith("✅ Ivan теперь администратор.")
    assert await services.admins.is_admin(4242)
    assert "@ivan" in nav.buttons()

    await nav.press("@ivan")
    assert "Источник: добавлен через бота" in nav.text
    assert "Кто добавил: Admin (@boss)" in nav.text
    assert "Уведомления о новых чатах: ✅ включены" in nav.text
    await nav.press("Не уведомлять")
    assert "Уведомления о новых чатах: ⏸ выключены" in nav.text
    assert await services.admins.new_chat_recipients() == [ADMIN_ID]
    await nav.press("Удалить из администраторов")
    await nav.press("Да, удалить")
    assert not await services.admins.is_admin(4242)

    await nav.press("🔒 @boss")
    assert "файл .env" in nav.text
    assert not any("Удалить" in b for b in nav.buttons())


async def test_new_admin_can_use_menu(
    nav: Nav, services: BotServices, tg: RecordingSession
) -> None:
    newcomer = User(id=4243, is_bot=False, first_name="New")
    await services.admins.add(TelegramUserData(telegram_user_id=4243), ADMIN_ID)
    person = Nav(nav.bot, tg, nav.settings, nav.services, Chat(id=4243, type="private"), newcomer)
    await person.send("/start")
    assert "Настройки SLA-бота" in person.text
