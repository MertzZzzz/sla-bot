from __future__ import annotations

from app.core.enums import Priority
from app.schemas.stats import ChatStatsRead, compliance_ratio
from app.services.formatting import TELEGRAM_MESSAGE_LIMIT
from app.services.stats import format_chat_stats, format_stats_report


def stats(**overrides: object) -> ChatStatsRead:
    data: dict[str, object] = {
        "chat_id": 1,
        "chat_title": "Поддержка VIP",
        "priority": Priority.P1,
        "responsible_telegram_id": 2000,
        "responsible_name": "Иван Иванов",
        "created": 91,
        "answered": 84,
        "answered_within_sla": 78,
        "overdue": 6,
        "not_required": 1,
        "cancelled": 0,
        "open_now": 2,
        "avg_response_seconds": 684.0,
        "median_response_seconds": 430.0,
        "p90_response_seconds": None,
    }
    data.update(overrides)
    return ChatStatsRead.model_validate(data)


def test_compliance_ratio() -> None:
    assert compliance_ratio(0, 0) is None
    assert compliance_ratio(10, 0) == 1.0
    assert compliance_ratio(0, 3) == 0.0
    assert round(compliance_ratio(78, 6) or 0, 3) == 0.929


def test_format_chat_stats() -> None:
    text = format_chat_stats(stats())
    assert "<b>Поддержка VIP</b> · P1" in text
    assert "Ответственный: Иван Иванов" in text
    assert "Создано: 91" in text
    assert "Ответов: 84" in text
    assert "Среднее время ответа: 11 мин 24 сек" in text
    assert "Медиана: 7 мин 10 сек" in text
    assert "P90: —" in text
    assert "Соблюдение SLA: 92.9%" in text
    assert "Просрочек: 6" in text
    assert "Не требуется: 1" in text
    assert "Открыто сейчас: 2" in text


def test_format_without_data() -> None:
    text = format_chat_stats(
        stats(
            created=0,
            answered=0,
            answered_within_sla=0,
            overdue=0,
            avg_response_seconds=None,
            median_response_seconds=None,
            responsible_telegram_id=None,
            responsible_name=None,
        )
    )
    assert "Соблюдение SLA: —" in text
    assert "Среднее время ответа: —" in text
    assert "Ответственный: не назначен" in text


def test_report_empty_and_paginated() -> None:
    assert "Нет данных" in format_stats_report([], 30)[0]
    many = [stats(chat_id=i, chat_title=f"Чат {i}") for i in range(100)]
    pages = format_stats_report(many, 7)
    assert len(pages) > 1
    assert all(len(p) <= TELEGRAM_MESSAGE_LIMIT for p in pages)
    assert pages[0].startswith("📊 <b>Статистика за 7 дн.</b>")
