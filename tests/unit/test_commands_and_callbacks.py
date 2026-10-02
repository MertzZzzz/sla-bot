from __future__ import annotations

import pytest

from app.schemas.callbacks import (
    MenuAction,
    MenuCallbackData,
    PendingReplyAction,
    PendingReplyCallbackData,
    ReassignAction,
    ReassignCallbackData,
)
from app.schemas.commands import (
    ChangeTimezoneCommand,
    CommandArgumentError,
    SetNotificationCommand,
    SlaInput,
    StatsCommand,
)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("45", 2700),
        ("1ч 30м", 5400),
        ("1h30m", 5400),
        ("2h", 7200),
        ("90s", 90),
        ("15 мин", 900),
        ("1d", 86400),
        ("30д", 30 * 86400),
    ],
)
def test_sla_input(text: str, seconds: int) -> None:
    assert SlaInput.parse(text).sla_seconds == seconds


@pytest.mark.parametrize("text", [None, "", "0", "abc", "1x", "31d", "5 минут назад", "-5"])
def test_sla_input_invalid(text: str | None) -> None:
    with pytest.raises(CommandArgumentError):
        SlaInput.parse(text)


def test_timezone_command() -> None:
    assert ChangeTimezoneCommand.parse("Europe/Berlin").timezone == "Europe/Berlin"
    with pytest.raises(CommandArgumentError):
        ChangeTimezoneCommand.parse("Nowhere/City")


def test_notification_command() -> None:
    cmd = SetNotificationCommand.parse("-1001234 15")
    assert (cmd.notification_chat_id, cmd.notification_thread_id) == (-1001234, 15)
    assert SetNotificationCommand.parse("-100999").notification_thread_id is None
    for bad in (None, "", "0", "abc", "-100 0", "-100 -3", "-100 1 2"):
        with pytest.raises(CommandArgumentError):
            SetNotificationCommand.parse(bad)


def test_stats_command() -> None:
    assert StatsCommand.parse(None).days == 30
    assert StatsCommand.parse("7").days == 7
    assert StatsCommand.parse("365").days == 365
    for bad in ("0", "366", "x", "1 2"):
        with pytest.raises(CommandArgumentError):
            StatsCommand.parse(bad)


def test_callback_data_roundtrip_and_size() -> None:
    packed = PendingReplyCallbackData(
        action=PendingReplyAction.NOT_REQUIRED, pending_reply_id=12345
    ).pack()
    assert packed == "pr:nr:12345"
    big = PendingReplyCallbackData(
        action=PendingReplyAction.NOT_REQUIRED, pending_reply_id=2**63 - 1
    ).pack()
    assert len(big.encode()) < 64
    parsed = PendingReplyCallbackData.unpack(packed)
    assert parsed.action is PendingReplyAction.NOT_REQUIRED
    assert parsed.pending_reply_id == 12345


def test_callback_data_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="zz"):
        PendingReplyCallbackData.unpack("pr:zz:1")


def test_reassign_callback_data() -> None:
    packed = ReassignCallbackData(
        action=ReassignAction.SET_CHAT, pending_reply_id=2**63 - 1, user_id=2**63 - 1
    ).pack()
    assert len(packed.encode()) < 64
    parsed = ReassignCallbackData.unpack("ra:st:12:345")
    assert (parsed.action, parsed.pending_reply_id, parsed.user_id) == (
        ReassignAction.SET_TICKET,
        12,
        345,
    )


def test_menu_callback_data_fits_telegram_limit() -> None:
    longest = MenuCallbackData(
        a=MenuAction.TIMEZONE_SET, i=2**63 - 1, v="America/Argentina/ComodRivadavia", p=999
    ).pack()
    assert len(longest.encode()) < 64
    user = MenuCallbackData(a=MenuAction.RESPONDER_TOGGLE, i=2**63 - 1, v=str(2**63 - 1), p=99)
    assert len(user.pack().encode()) < 64
    parsed = MenuCallbackData.unpack("m:ss:3:900:0")
    assert (parsed.a, parsed.i, parsed.v, parsed.p) == (MenuAction.SLA_SET, 3, "900", 0)
