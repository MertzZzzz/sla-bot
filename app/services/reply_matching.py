"""Pure rules that decide which open ticket a responder message may close."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import PendingReplyStatus, ReplyMatchMode


class MatchKind(StrEnum):
    OLDEST_IN_CHAT = "oldest_in_chat"
    EXACT_SOURCE = "exact_source"
    OLDEST_IN_THREAD = "oldest_in_thread"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class MatchCriteria:
    kind: MatchKind
    source_message_id: int | None = None
    thread_id: int | None = None


def explicit_reply_target(
    reply_to_message_id: int | None, message_thread_id: int | None, is_topic_message: bool
) -> int | None:
    """Return the message the user explicitly replied to.

    In forum topics Telegram sets ``reply_to_message`` to the topic's root service
    message for every plain message; that is not an explicit reply.
    """
    if reply_to_message_id is None:
        return None
    if (
        is_topic_message
        and message_thread_id is not None
        and reply_to_message_id == message_thread_id
    ):
        return None
    return reply_to_message_id


def build_match_criteria(
    mode: ReplyMatchMode,
    *,
    reply_to_message_id: int | None,
    message_thread_id: int | None,
    is_topic_message: bool,
) -> MatchCriteria:
    reply_target = explicit_reply_target(reply_to_message_id, message_thread_id, is_topic_message)
    match mode:
        case ReplyMatchMode.ANY_RESPONDER_MESSAGE:
            return MatchCriteria(MatchKind.OLDEST_IN_CHAT)
        case ReplyMatchMode.REPLY_ONLY:
            if reply_target is None:
                return MatchCriteria(MatchKind.NONE)
            return MatchCriteria(MatchKind.EXACT_SOURCE, source_message_id=reply_target)
        case ReplyMatchMode.THREAD_OR_REPLY:
            if reply_target is not None:
                return MatchCriteria(MatchKind.EXACT_SOURCE, source_message_id=reply_target)
            return MatchCriteria(MatchKind.OLDEST_IN_THREAD, thread_id=message_thread_id)
    msg = f"Unsupported reply match mode: {mode}"  # pragma: no cover
    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TicketView:
    """Minimal ticket state for in-memory matching (used by tests and docs)."""

    id: int
    source_message_id: int
    source_thread_id: int | None
    status: PendingReplyStatus


def select_ticket(
    tickets: list[TicketView], criteria: MatchCriteria, response_message_id: int
) -> TicketView | None:
    """Reference implementation of the SQL in ``PendingReplyRepository.find_for_answer``."""
    candidates = [
        t
        for t in tickets
        if t.status in PendingReplyStatus.open_statuses()
        and t.source_message_id < response_message_id
    ]
    match criteria.kind:
        case MatchKind.NONE:
            return None
        case MatchKind.EXACT_SOURCE:
            candidates = [
                t for t in candidates if t.source_message_id == criteria.source_message_id
            ]
        case MatchKind.OLDEST_IN_THREAD:
            candidates = [t for t in candidates if t.source_thread_id == criteria.thread_id]
        case MatchKind.OLDEST_IN_CHAT:
            pass
    return min(candidates, key=lambda t: t.source_message_id, default=None)


class InvalidTransitionError(Exception):
    pass


ALLOWED_TRANSITIONS: dict[PendingReplyStatus, frozenset[PendingReplyStatus]] = {
    PendingReplyStatus.WAITING: frozenset(
        {
            PendingReplyStatus.OVERDUE,
            PendingReplyStatus.ANSWERED,
            PendingReplyStatus.NOT_REQUIRED,
            PendingReplyStatus.CANCELLED,
        }
    ),
    PendingReplyStatus.OVERDUE: frozenset(
        {PendingReplyStatus.ANSWERED, PendingReplyStatus.NOT_REQUIRED, PendingReplyStatus.CANCELLED}
    ),
    PendingReplyStatus.ANSWERED: frozenset(),
    PendingReplyStatus.NOT_REQUIRED: frozenset(),
    PendingReplyStatus.CANCELLED: frozenset(),
}


def can_transition(current: PendingReplyStatus, target: PendingReplyStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: PendingReplyStatus, target: PendingReplyStatus) -> None:
    if not can_transition(current, target):
        msg = f"Transition {current} -> {target} is not allowed"
        raise InvalidTransitionError(msg)
