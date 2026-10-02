"""Delivery port used by the worker; the aiogram adapter lives in ``app.bot.sender``."""

from __future__ import annotations

from typing import Protocol


class DeliveryError(Exception):
    def __init__(self, message: str, *, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class TransientDeliveryError(DeliveryError):
    """Network problems, 5xx, flood control: safe to retry later."""

    def __init__(self, message: str, *, error_type: str, retry_after: float | None = None) -> None:
        super().__init__(message, error_type=error_type)
        self.retry_after = retry_after


class PermanentDeliveryError(DeliveryError):
    """Chat not found, bot kicked, missing rights, bad request: retrying will not help."""


class NotificationSender(Protocol):
    def send_notification(
        self, *, chat_id: int, thread_id: int | None, text: str, pending_reply_id: int
    ) -> int:
        """Send an HTML notification with the "not required" button; return its message ID."""
        ...

    def send_text(self, *, chat_id: int, text: str) -> None:
        """Plain HTML message without buttons (technical alerts)."""
        ...
