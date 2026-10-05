from __future__ import annotations

from datetime import date

import pytest

from app.bot.menus import pilot_dates_lines
from app.core.enums import ChatType, Priority, ReplyMatchMode
from app.schemas.chats import MonitoredChatRead
from app.schemas.commands import CommandArgumentError, DateInput
from tests.factories import T0

TODAY = date(2026, 10, 5)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("01.11.2026", date(2026, 11, 1)),
        ("1.11.26", date(2026, 11, 1)),
        ("2026-11-01", date(2026, 11, 1)),
        ("01/11/2026", date(2026, 11, 1)),
        ("сегодня", TODAY),
        ("Завтра", date(2026, 10, 6)),
        ("-", None),
        ("очистить", None),
    ],
)
def test_date_input(text: str, expected: date | None) -> None:
    assert DateInput.parse(text, TODAY).value == expected


@pytest.mark.parametrize("text", [None, "", "31.02.2026", "завтра-послезавтра", "11/2026"])
def test_date_input_invalid(text: str | None) -> None:
    with pytest.raises(CommandArgumentError):
        DateInput.parse(text, TODAY)


def chat(start: date | None, end: date | None) -> MonitoredChatRead:
    return MonitoredChatRead(
        id=1,
        telegram_chat_id=-100,
        title="X",
        chat_type=ChatType.SUPERGROUP,
        is_enabled=True,
        priority=Priority.P3,
        sla_seconds=900,
        reply_match_mode=ReplyMatchMode.ANY_RESPONDER_MESSAGE,
        responsible_user_id=None,
        notification_chat_id=None,
        notification_thread_id=None,
        timezone="Europe/Moscow",
        pilot_start=start,
        pilot_end=end,
        created_at=T0,
        updated_at=T0,
    )


def test_pilot_dates_lines() -> None:
    assert pilot_dates_lines(chat(None, None), TODAY) == [
        "Начало пилота: <i>не задано</i>",
        "Окончание пилота: <i>не задано</i>",
    ]
    running = pilot_dates_lines(chat(date(2026, 10, 1), date(2026, 10, 31)), TODAY)
    assert running == [
        "Начало пилота: 01.10.2026",
        "Окончание пилота: 31.10.2026 (осталось 26 дн.)",
    ]
    future = pilot_dates_lines(chat(date(2026, 10, 10), None), TODAY)
    assert future[1] == "Окончание пилота: <i>не задано</i> (начнётся через 5 дн.)"
    done = pilot_dates_lines(chat(None, date(2026, 10, 1)), TODAY)
    assert done[1] == "Окончание пилота: 01.10.2026 (завершён)"
