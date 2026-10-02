from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.filters.is_monitored_chat import IsMonitoredChat
from app.bot.services import BotServices
from app.bot.texts import NOT_ADMIN, NOT_MONITORED, pending_text
from app.schemas.chats import MonitoredChatDetails
from app.schemas.commands import CommandArgumentError, StatsCommand
from app.schemas.stats import StatsQuery
from app.services.stats import format_stats_report

router = Router(name="stats")


@router.message(Command("stats", "pending"), ~IsGlobalAdmin())
async def refuse(message: Message) -> None:
    await message.reply(NOT_ADMIN)


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = StatsCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    stats = await services.stats.collect(StatsQuery(days=args.days))
    for chunk in format_stats_report(stats, args.days):
        await message.answer(chunk)


@router.message(Command("pending"), IsMonitoredChat())
async def cmd_pending(
    message: Message, services: BotServices, monitored: MonitoredChatDetails
) -> None:
    tickets = await services.pending.list_open(message.chat.id)
    for chunk in pending_text(tickets, monitored.chat.timezone):
        await message.answer(chunk)


@router.message(Command("pending"))
async def cmd_pending_not_monitored(message: Message) -> None:
    await message.reply(NOT_MONITORED)
