from __future__ import annotations

from pydantic import Field

from app.core.enums import Priority
from app.schemas.common import Schema


class StatsQuery(Schema):
    days: int = Field(default=30, ge=1, le=365)


class ChatStatsRead(Schema):
    chat_id: int
    chat_title: str
    priority: Priority
    responsible_telegram_id: int | None
    responsible_name: str | None
    created: int
    answered: int
    answered_within_sla: int
    overdue: int
    not_required: int
    cancelled: int
    open_now: int
    avg_response_seconds: float | None
    median_response_seconds: float | None
    p90_response_seconds: float | None

    @property
    def sla_compliance(self) -> float | None:
        return compliance_ratio(self.answered_within_sla, self.overdue)


def compliance_ratio(answered_within_sla: int, breached: int) -> float | None:
    """SLA compliance = answered within SLA / (answered within SLA + breached).

    ``breached`` includes tickets answered late and tickets still overdue; tickets
    marked ``not_required``/``cancelled`` are excluded by the caller. Returns ``None``
    when there is nothing to measure (avoids division by zero).
    """
    total = answered_within_sla + breached
    if total <= 0:
        return None
    return answered_within_sla / total
