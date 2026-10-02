from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message, Update

from app.bot.extractors import extract_incoming
from app.bot.services import BotServices

router = Router(name="messages")


@router.message(F.migrate_to_chat_id)
async def on_group_migrated(message: Message, services: BotServices) -> None:
    assert message.migrate_to_chat_id is not None
    await services.chats.migrate_chat(message.chat.id, message.migrate_to_chat_id)


@router.message()
async def on_group_message(message: Message, services: BotServices, event_update: Update) -> None:
    incoming = extract_incoming(message, update_id=event_update.update_id)
    if incoming is None:
        return
    await services.messages.process(incoming)
