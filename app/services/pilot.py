"""Pilot participants: people to invite into every customer chat on demand."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.db.models import PilotParticipant
from app.db.uow import UnitOfWork
from app.schemas.users import TelegramUserData


@dataclass(frozen=True, slots=True)
class PilotView:
    id: int
    username: str | None
    telegram_user_id: int | None
    display_name: str | None
    added_by_telegram_id: int | None
    created_at: datetime

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        return self.display_name or str(self.telegram_user_id)


@dataclass(frozen=True, slots=True)
class AddResult:
    added: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)


def _view(row: PilotParticipant) -> PilotView:
    return PilotView(
        id=row.id,
        username=row.username,
        telegram_user_id=row.telegram_user_id,
        display_name=row.display_name,
        added_by_telegram_id=row.added_by_telegram_id,
        created_at=row.created_at,
    )


class PilotService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def list_all(self) -> list[PilotView]:
        async with self._uow_factory() as uow:
            return [_view(r) for r in await uow.pilot.list_all()]

    async def get(self, participant_id: int) -> PilotView | None:
        async with self._uow_factory() as uow:
            row = await uow.pilot.get(participant_id)
            return _view(row) if row else None

    async def add(self, people: Iterable[TelegramUserData | str], actor_id: int) -> AddResult:
        """Add users and/or bare usernames; usernames the bot already knows get an ID."""
        result = AddResult()
        async with self._uow_factory() as uow:
            for person in people:
                if isinstance(person, str):
                    username: str | None = person.lstrip("@")
                    known = await uow.users.get_by_username(username or "")
                    user_id = known.telegram_user_id if known else None
                    name = known.display_name if known else None
                    if known and known.username:
                        username = known.username
                else:
                    username, user_id = person.username, person.telegram_user_id
                    name = person.display_name
                row = await uow.pilot.add(
                    username=username,
                    telegram_user_id=user_id,
                    display_name=name,
                    added_by=actor_id,
                )
                label = f"@{username}" if username else (name or str(user_id))
                (result.added if row else result.already).append(label)
            await uow.commit()
        return result

    async def remove(self, participant_id: int) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.pilot.remove(participant_id)
            await uow.commit()
        return removed

    async def link_user(self, user: TelegramUserData) -> bool:
        """Remember who a listed @username is once they show up; True if listed."""
        async with self._uow_factory() as uow:
            row = await uow.pilot.find(user.telegram_user_id, user.username)
            if row is None:
                return False
            if row.telegram_user_id is None:
                row.telegram_user_id = user.telegram_user_id
            row.display_name = user.display_name
            if user.username:
                row.username = user.username
            await uow.commit()
        return True

    async def resolve_all(self) -> list[PilotView]:
        """Participants with IDs filled from users the bot has seen since they were added."""
        async with self._uow_factory() as uow:
            rows = await uow.pilot.list_all()
            for row in rows:
                if row.telegram_user_id is None and row.username:
                    known = await uow.users.get_by_username(row.username)
                    if known is not None:
                        row.telegram_user_id = known.telegram_user_id
                        row.display_name = known.display_name
            await uow.commit()
            return [_view(r) for r in rows]
