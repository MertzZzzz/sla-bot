from __future__ import annotations

import pytest

from app.core.enums import PendingReplyStatus as S
from app.core.enums import ReplyMatchMode as M
from app.services.reply_matching import (
    InvalidTransitionError,
    MatchCriteria,
    MatchKind,
    TicketView,
    build_match_criteria,
    can_transition,
    ensure_transition,
    explicit_reply_target,
    select_ticket,
)

TICKETS = [
    TicketView(1, source_message_id=10, source_thread_id=None, status=S.ANSWERED),
    TicketView(2, source_message_id=11, source_thread_id=None, status=S.OVERDUE),
    TicketView(3, source_message_id=12, source_thread_id=100, status=S.WAITING),
    TicketView(4, source_message_id=13, source_thread_id=None, status=S.WAITING),
    TicketView(5, source_message_id=14, source_thread_id=100, status=S.WAITING),
    TicketView(6, source_message_id=50, source_thread_id=None, status=S.WAITING),
]


def criteria(
    mode: M, reply_to: int | None = None, thread: int | None = None, topic: bool = False
) -> MatchCriteria:
    return build_match_criteria(
        mode, reply_to_message_id=reply_to, message_thread_id=thread, is_topic_message=topic
    )


def test_forum_topic_root_is_not_an_explicit_reply() -> None:
    assert explicit_reply_target(100, 100, is_topic_message=True) is None
    assert explicit_reply_target(12, 100, is_topic_message=True) == 12
    assert explicit_reply_target(100, 100, is_topic_message=False) == 100
    assert explicit_reply_target(None, None, is_topic_message=False) is None


def test_any_responder_closes_oldest_open_before_response() -> None:
    chosen = select_ticket(TICKETS, criteria(M.ANY_RESPONDER_MESSAGE), response_message_id=20)
    assert chosen is not None and chosen.id == 2  # oldest open, answered #1 skipped


def test_any_responder_ignores_later_messages() -> None:
    chosen = select_ticket(TICKETS[5:], criteria(M.ANY_RESPONDER_MESSAGE), response_message_id=20)
    assert chosen is None


def test_reply_only_requires_exact_reply() -> None:
    assert criteria(M.REPLY_ONLY).kind is MatchKind.NONE
    assert select_ticket(TICKETS, criteria(M.REPLY_ONLY), 20) is None
    chosen = select_ticket(TICKETS, criteria(M.REPLY_ONLY, reply_to=13), 20)
    assert chosen is not None and chosen.id == 4


def test_reply_only_to_closed_ticket_matches_nothing() -> None:
    assert select_ticket(TICKETS, criteria(M.REPLY_ONLY, reply_to=10), 20) is None


def test_reply_only_topic_root_reply_is_not_a_reply() -> None:
    c = criteria(M.REPLY_ONLY, reply_to=100, thread=100, topic=True)
    assert c.kind is MatchKind.NONE


def test_thread_or_reply_exact_reply_wins() -> None:
    chosen = select_ticket(
        TICKETS, criteria(M.THREAD_OR_REPLY, reply_to=14, thread=100, topic=True), 20
    )
    assert chosen is not None and chosen.id == 5


def test_thread_or_reply_falls_back_to_oldest_in_thread() -> None:
    c = criteria(M.THREAD_OR_REPLY, reply_to=100, thread=100, topic=True)
    assert c == MatchCriteria(MatchKind.OLDEST_IN_THREAD, thread_id=100)
    chosen = select_ticket(TICKETS, c, 20)
    assert chosen is not None and chosen.id == 3


def test_thread_or_reply_general_thread() -> None:
    chosen = select_ticket(TICKETS, criteria(M.THREAD_OR_REPLY), 20)
    assert chosen is not None and chosen.id == 2


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (S.WAITING, S.OVERDUE, True),
        (S.WAITING, S.ANSWERED, True),
        (S.OVERDUE, S.ANSWERED, True),
        (S.OVERDUE, S.NOT_REQUIRED, True),
        (S.OVERDUE, S.WAITING, False),
        (S.ANSWERED, S.NOT_REQUIRED, False),
        (S.NOT_REQUIRED, S.ANSWERED, False),
        (S.CANCELLED, S.OVERDUE, False),
    ],
)
def test_transitions(current: S, target: S, allowed: bool) -> None:
    assert can_transition(current, target) is allowed
    if not allowed:
        with pytest.raises(InvalidTransitionError):
            ensure_transition(current, target)
