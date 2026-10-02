from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot.filters.is_global_admin import IsGlobalAdmin
from app.bot.filters.settings_context import IsSettingsChat
from app.bot.services import BotServices
from app.schemas.commands import CommandArgumentError, StatsCommand
from app.schemas.stats import StatsQuery
from app.services.stats import format_stats_report

router = Router(name="stats")


@router.message(Command("stats"), IsSettingsChat(), IsGlobalAdmin())
async def cmd_stats(message: Message, command: CommandObject, services: BotServices) -> None:
    try:
        args = StatsCommand.parse(command.args)
    except CommandArgumentError as exc:
        await message.reply(str(exc))
        return
    stats = await services.stats.collect(StatsQuery(days=args.days))
    for chunk in format_stats_report(stats, args.days):
        await message.answer(chunk)
