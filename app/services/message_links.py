from __future__ import annotations

SUPERGROUP_PREFIX = -1_000_000_000_000


class MessageLinkService:
    """Builds ``t.me`` links to group messages.

    Links exist only for supergroups: public ones via ``t.me/<username>/<id>``,
    private ones via ``t.me/c/<internal_id>/<id>`` (opens for chat members only).
    Basic groups have no message links.
    """

    def build(
        self,
        chat_id: int,
        message_id: int,
        *,
        thread_id: int | None = None,
        chat_username: str | None = None,
    ) -> str | None:
        if message_id <= 0:
            return None
        topic = f"{thread_id}/" if thread_id else ""
        if chat_username and chat_username.replace("_", "").isalnum():
            return f"https://t.me/{chat_username}/{topic}{message_id}"
        if chat_id >= SUPERGROUP_PREFIX:
            return None
        internal_id = -chat_id + SUPERGROUP_PREFIX  # -100123 -> 123
        if internal_id <= 0:
            return None
        return f"https://t.me/c/{internal_id}/{topic}{message_id}"
