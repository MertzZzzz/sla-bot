from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.enums import ContentType, Priority
from app.schemas.notifications import NotificationPayload
from app.schemas.pending_replies import IncomingMessage
from app.schemas.users import TelegramUserData

T0 = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)
CHAT_ID = -1001234567890


@dataclass
class FakeClock:
    current: datetime = field(default=T0)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def user(uid: int, first: str = "User", username: str | None = None) -> TelegramUserData:
    return TelegramUserData(telegram_user_id=uid, first_name=f"{first}{uid}", username=username)


def incoming(
    message_id: int,
    author: TelegramUserData,
    *,
    chat_id: int = CHAT_ID,
    date: datetime = T0,
    text: str | None = "Здравствуйте, есть вопрос",
    reply_to: int | None = None,
    thread_id: int | None = None,
    is_topic: bool = False,
    update_id: int | None = None,
    content_type: ContentType = ContentType.TEXT,
    title: str = "Поддержка VIP",
) -> IncomingMessage:
    return IncomingMessage(
        update_id=update_id,
        telegram_chat_id=chat_id,
        chat_title=title,
        chat_type="supergroup",
        message_id=message_id,
        message_thread_id=thread_id,
        is_topic_message=is_topic,
        reply_to_message_id=reply_to,
        date=date,
        author=author,
        text=text,
        content_type=content_type,
    )


def payload(**overrides: object) -> NotificationPayload:
    data: dict[str, object] = {
        "pending_reply_id": 42,
        "target_chat_id": -100555,
        "target_thread_id": 7,
        "chat_title": "Поддержка VIP",
        "priority": Priority.P1,
        "sla_seconds": 900,
        "responsible_telegram_id": 2000,
        "responsible_name": "Иван Иванов",
        "author_name": "Клиент",
        "author_telegram_id": 3000,
        "source_text": "Где мой заказ?",
        "source_content_type": "text",
        "source_message_date": T0,
        "deadline_at": T0 + timedelta(minutes=15),
        "message_link": "https://t.me/c/1234567890/10",
        "timezone": "Europe/Moscow",
    }
    data.update(overrides)
    return NotificationPayload.model_validate(data)
