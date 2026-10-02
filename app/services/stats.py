from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from app.db.repositories.stats import RawChatStats
from app.db.uow import UnitOfWork
from app.schemas.stats import ChatStatsRead, StatsQuery
from app.services.clock import Clock
from app.services.formatting import (
    escape,
    format_duration,
    format_percent,
    split_message,
)


class StatsService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def collect(self, query: StatsQuery) -> list[ChatStatsRead]:
        now = self._clock.now()
        since = now - timedelta(days=query.days)
        async with self._uow_factory() as uow:
            raw = await uow.stats.period_stats(since, now)
            open_counts = await uow.stats.open_counts()
            keys = {(r.chat_id, r.responsible_telegram_id) for r in raw} | set(open_counts)
            by_key = {(r.chat_id, r.responsible_telegram_id): r for r in raw}
            users = await uow.users.map_by_telegram_ids(k[1] for k in keys if k[1] is not None)
            result: list[ChatStatsRead] = []
            for chat_id, responsible_id in keys:
                chat = await uow.chats.get(chat_id)
                if chat is None:  # pragma: no cover - FK guarantees existence
                    continue
                stats = by_key.get((chat_id, responsible_id)) or _empty(chat_id, responsible_id)
                user = users.get(responsible_id) if responsible_id is not None else None
                result.append(
                    ChatStatsRead(
                        chat_id=chat_id,
                        chat_title=chat.title,
                        priority=chat.priority,
                        responsible_telegram_id=responsible_id,
                        responsible_name=user.display_name if user else None,
                        open_now=open_counts.get((chat_id, responsible_id), 0),
                        **{k: getattr(stats, k) for k in _RAW_FIELDS},
                    )
                )
        return sorted(
            result, key=lambda s: (s.priority.value, s.chat_title, s.responsible_name or "")
        )


_RAW_FIELDS = (
    "created",
    "answered",
    "answered_within_sla",
    "overdue",
    "not_required",
    "cancelled",
    "avg_response_seconds",
    "median_response_seconds",
    "p90_response_seconds",
)


def _empty(chat_id: int, responsible_id: int | None) -> RawChatStats:
    return RawChatStats(chat_id, responsible_id, 0, 0, 0, 0, 0, 0, None, None, None)


def _duration(value: float | None) -> str:
    return "—" if value is None else format_duration(value)


def format_chat_stats(stats: ChatStatsRead) -> str:
    responsible = escape(
        stats.responsible_name
        or (
            "не назначен"
            if stats.responsible_telegram_id is None
            else str(stats.responsible_telegram_id)
        )
    )
    return "\n".join(
        [
            f"<b>{escape(stats.chat_title)}</b> · {stats.priority.label}",
            f"Ответственный: {responsible}",
            f"Создано: {stats.created}",
            f"Ответов: {stats.answered}",
            f"Среднее время ответа: {_duration(stats.avg_response_seconds)}",
            f"Медиана: {_duration(stats.median_response_seconds)}",
            f"P90: {_duration(stats.p90_response_seconds)}",
            f"Соблюдение SLA: {format_percent(stats.sla_compliance)}",
            f"Просрочек: {stats.overdue}",
            f"Не требуется: {stats.not_required}",
            f"Открыто сейчас: {stats.open_now}",
        ]
    )


def format_stats_report(stats: list[ChatStatsRead], days: int) -> list[str]:
    header = f"📊 <b>Статистика за {days} дн.</b>"
    if not stats:
        return [f"{header}\n\nНет данных за выбранный период."]
    return split_message([header, *(format_chat_stats(s) for s in stats)])
