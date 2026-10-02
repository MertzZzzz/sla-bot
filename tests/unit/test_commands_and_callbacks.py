from __future__ import annotations

import pytest

from app.core.enums import Priority, ReplyMatchMode
from app.schemas.callbacks import PendingReplyAction, PendingReplyCallbackData
from app.schemas.commands import (
    ChangeModeCommand,
    ChangePriorityCommand,
    ChangeSlaCommand,
    ChangeTimezoneCommand,
    CommandArgumentError,
    SetNotificationCommand,
    StatsCommand,
)


def test_sla_command() -> None:
    assert ChangeSlaCommand.parse("900").sla_seconds == 900
    for bad in (None, "", "0", "-5", "abc", "1 2", "99999999"):
        with pytest.raises(CommandArgumentError):
            ChangeSlaCommand.parse(bad)


def test_priority_command() -> None:
    assert ChangePriorityCommand.parse("P1").priority is Priority.P1
    assert ChangePriorityCommand.parse("p4").priority is Priority.P4
    for bad in (None, "p5", "high", "p1 p2"):
        with pytest.raises(CommandArgumentError):
            ChangePriorityCommand.parse(bad)


def test_mode_command() -> None:
    assert ChangeModeCommand.parse("reply_only").mode is ReplyMatchMode.REPLY_ONLY
    with pytest.raises(CommandArgumentError, match="any_responder_message"):
        ChangeModeCommand.parse("whatever")


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
