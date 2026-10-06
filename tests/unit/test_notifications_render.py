from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.config import CelerySettings
from app.services.formatting import TELEGRAM_MESSAGE_LIMIT
from app.services.notifications import (
    NotificationConfigError,
    backoff_seconds,
    not_required_line,
    reassigned_line,
    render_notification,
    replace_responsible_line,
    responsible_label,
)
from tests.factories import T0, payload


def test_render_contains_required_fields() -> None:
    now = T0 + timedelta(minutes=20)
    rendered = render_notification(payload(), now)
    text = rendered.text
    assert text.startswith("🔴 <b>SLA нарушен</b>")
    # The chat title links to the chat (falls back to the message link).
    assert 'Чат: <a href="https://t.me/c/1234567890/10"><b>Поддержка VIP</b></a>' in text
    assert "Приоритет: P1" in text
    assert "SLA: 15 мин" in text
    assert 'Ответственный: <a href="tg://user?id=2000">Иван Иванов</a>' in text
    assert "Сообщение от: Клиент" in text
    assert "Получено: 15.01.2026 12:00:00 (Europe/Moscow)" in text
    assert "Просрочка: 5 мин" in text
    assert "Текст: Где мой заказ?" in text
    assert '<a href="https://t.me/c/1234567890/10">Открыть сообщение</a>' in text
    assert rendered.target_chat_id == -100555
    assert rendered.target_thread_id == 7


def test_render_escapes_user_content() -> None:
    p = payload(
        source_text="<script>alert(1)</script> & co",
        chat_title="<b>Chat</b>",
        author_name="<i>Evil</i>",
        responsible_name="<u>Resp</u>",
    )
    text = render_notification(p, T0).text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;Chat&lt;/b&gt;" in text
    assert "&lt;i&gt;Evil&lt;/i&gt;" in text
    assert "&lt;u&gt;Resp&lt;/u&gt;" in text


def test_render_respects_telegram_limit_even_after_escaping() -> None:
    text = render_notification(payload(source_text="&<>" * 5000), T0).text
    assert len(text) <= TELEGRAM_MESSAGE_LIMIT


def test_render_without_responsible_link_and_text() -> None:
    p = payload(
        responsible_telegram_id=None,
        responsible_name=None,
        message_link=None,
        source_text=None,
        source_content_type="photo",
    )
    text = render_notification(p, T0).text
    assert "Ответственный: не назначен" in text
    assert "Текст: [Фото]" in text
    assert "Открыть сообщение" not in text


def test_render_requires_target_chat() -> None:
    with pytest.raises(NotificationConfigError):
        render_notification(payload(target_chat_id=None), T0)


def test_not_required_line() -> None:
    line = not_required_line("<Админ>", 1000)
    assert line == '✅ Ответ не требуется. Отметил: <a href="tg://user?id=1000">&lt;Админ&gt;</a>'


def test_backoff_is_exponential_and_capped() -> None:
    settings = CelerySettings(retry_backoff_base_seconds=5, retry_backoff_max_seconds=60)
    assert [backoff_seconds(a, settings) for a in (1, 2, 3, 4, 5)] == [5, 10, 20, 40, 60]
    assert backoff_seconds(1, settings, retry_after=30) == 30


def test_replace_responsible_line_and_reassigned_line() -> None:
    text = render_notification(payload(), T0).text
    updated = replace_responsible_line(text, 4000, "<Пётр>")
    assert 'Ответственный: <a href="tg://user?id=4000">&lt;Пётр&gt;</a>' in updated
    assert "tg://user?id=2000" not in updated
    assert updated.count("\n") == text.count("\n")
    assert reassigned_line(chat_scope=False, old="A", new="B", actor="C") == (
        "🔁 Ответственный по сообщению: A → B. Изменил: C"
    )
    assert "для новых сообщений" in reassigned_line(chat_scope=True, old="A", new="B", actor="C")
    assert responsible_label(None, None) == "не назначен"


def test_render_merged_messages() -> None:
    p = payload(
        message_count=3,
        last_message_text="<ещё>",
        last_content_type="text",
        last_message_date=T0 + timedelta(minutes=4),
    )
    text = render_notification(p, T0 + timedelta(minutes=20)).text
    assert "Сообщений: 3 (последнее 15.01.2026 12:04:00)" in text
    assert "Текст: Где мой заказ?\nПоследнее: &lt;ещё&gt;" in text
    single = render_notification(payload(), T0).text
    assert "Сообщений:" not in single
    assert "Последнее:" not in single
    huge = payload(message_count=2, source_text="&" * 5000, last_message_text="<" * 5000)
    assert len(render_notification(huge, T0).text) <= TELEGRAM_MESSAGE_LIMIT


def test_chat_title_links_to_chat() -> None:
    linked = render_notification(payload(chat_link="https://t.me/+AbCdEf"), T0).text
    assert 'Чат: <a href="https://t.me/+AbCdEf"><b>Поддержка VIP</b></a>' in linked
    plain = render_notification(payload(chat_link=None, message_link=None), T0).text
    assert "Чат: <b>Поддержка VIP</b>" in plain
    escaped = render_notification(payload(chat_link='https://t.me/x"><script>'), T0).text
    assert "<script>" not in escaped


@pytest.mark.parametrize(
    ("percent", "title"),
    [
        (50, "🟡 <b>SLA: прошла половина времени</b>"),
        (60, "🟡 <b>SLA: прошло 60% времени</b>"),
        (75, "🟠 <b>SLA скоро истечёт</b> — прошло 75% времени"),
    ],
)
def test_render_warning(percent: int, title: str) -> None:
    now = T0 + timedelta(minutes=15 * percent / 100)
    text = render_notification(payload(kind="warning", warning_percent=percent), now).text
    assert text.startswith(title)
    assert "Осталось:" in text
    assert "Просрочка" not in text


def test_old_payload_without_kind_is_a_breach() -> None:
    assert render_notification(payload(), T0 + timedelta(minutes=20)).text.startswith(
        "🔴 <b>SLA нарушен</b>"
    )
