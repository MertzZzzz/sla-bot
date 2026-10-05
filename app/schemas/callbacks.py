from __future__ import annotations

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class PendingReplyAction(StrEnum):
    NOT_REQUIRED = "nr"


class PendingReplyCallbackData(CallbackData, prefix="pr"):
    """Packs to ``pr:nr:<id>`` — well below Telegram's 64-byte limit."""

    action: PendingReplyAction
    pending_reply_id: int


class ReassignAction(StrEnum):
    MENU_TICKET = "mt"  # show candidates for this message's responsible
    MENU_CHAT = "mc"  # show candidates for the chat's responsible
    SET_TICKET = "st"
    SET_CHAT = "sc"
    BACK = "bk"


class ReassignCallbackData(CallbackData, prefix="ra"):
    """Packs to ``ra:st:<ticket_id>:<telegram_user_id>`` — under 64 bytes for any IDs."""

    action: ReassignAction
    pending_reply_id: int
    user_id: int = 0


class MenuAction(StrEnum):
    HOME = "h"
    NOOP = "nop"
    CANCEL = "x"
    # administrators
    ADMINS = "al"
    ADMIN = "ac"
    ADMIN_ADD = "aa"
    ADMIN_NOTIFY = "an"
    ADMIN_DELETE = "ad"
    ADMIN_DELETE_CONFIRM = "ay"
    # pilot participants
    PILOT = "pl"
    PILOT_ITEM = "pi"
    PILOT_ADD = "pa"
    PILOT_DELETE = "pd"
    PILOT_DELETE_CONFIRM = "py"
    INVITE = "iv"
    INVITE_RUN = "ir"
    # chats
    CHATS = "cl"
    CHAT = "cc"
    CHAT_ADD_PICK = "ap"  # native group picker
    CHAT_ADD_CONFIRM = "aq"  # v=<telegram chat id>, p=<topic id or 0 for the whole group>
    ENABLE = "en"
    PRIORITY = "pm"
    PRIORITY_SET = "ps"
    SLA = "sm"
    SLA_SET = "ss"
    SLA_INPUT = "si"
    MODE = "mm"
    MODE_SET = "ms"
    TIMEZONE = "tm"
    TIMEZONE_SET = "ts"
    TIMEZONE_INPUT = "ti"
    RESPONSIBLE = "rm"
    RESPONSIBLE_SET = "rs"
    RESPONSIBLE_CLEAR = "rx"
    RESPONSIBLE_INPUT = "ri"
    RESPONDERS = "dm"
    RESPONDER_TOGGLE = "dt"
    RESPONDER_INPUT = "di"
    NOTIFICATIONS = "nm"
    NOTIFICATIONS_HERE = "nh"
    NOTIFICATIONS_PICK = "ng"
    NOTIFICATIONS_INPUT = "ni"
    NOTIFICATIONS_CLEAR = "nx"
    OPEN_TICKETS = "op"
    DATES = "ds"
    DATES_INPUT = "de"  # v=start|end
    DATES_CLEAR = "dc"  # v=start|end
    CHAT_DELETE = "cd"
    CHAT_DELETE_CONFIRM = "cy"


class MenuCallbackData(CallbackData, prefix="m"):
    """Settings menu navigation: ``m:<action>:<id>:<value>:<page>`` (< 64 bytes).

    ``id`` is the internal monitored chat ID for chat screens or the Telegram user ID
    for administrator screens.
    """

    a: MenuAction
    i: int = 0
    v: str = ""
    p: int = 0


class NotifyHereCallbackData(CallbackData, prefix="nt"):
    """/notify_here in a group: route notifications of monitored chat ``chat`` here."""

    chat: int
