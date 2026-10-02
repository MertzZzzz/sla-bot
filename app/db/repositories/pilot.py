from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PilotParticipant


class PilotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[PilotParticipant]:
        stmt = select(PilotParticipant).order_by(PilotParticipant.created_at, PilotParticipant.id)
        return list(await self._session.scalars(stmt))

    async def get(self, participant_id: int) -> PilotParticipant | None:
        return await self._session.get(PilotParticipant, participant_id)

    async def add(
        self,
        *,
        username: str | None,
        telegram_user_id: int | None,
        display_name: str | None,
        added_by: int,
    ) -> PilotParticipant | None:
        """Insert; ``None`` when the same username or user ID is already listed."""
        stmt = (
            insert(PilotParticipant)
            .values(
                username=username,
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                added_by_telegram_id=added_by,
            )
            .on_conflict_do_nothing()
            .returning(PilotParticipant)
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def remove(self, participant_id: int) -> bool:
        result = await self._session.execute(
            delete(PilotParticipant)
            .where(PilotParticipant.id == participant_id)
            .returning(PilotParticipant.id)
        )
        return result.scalar_one_or_none() is not None

    async def find(self, telegram_user_id: int, username: str | None) -> PilotParticipant | None:
        conditions = [PilotParticipant.telegram_user_id == telegram_user_id]
        if username:
            conditions.append(func.lower(PilotParticipant.username) == username.lower())
        stmt = select(PilotParticipant).where(or_(*conditions)).limit(1).with_for_update()
        participant: PilotParticipant | None = await self._session.scalar(stmt)
        return participant
