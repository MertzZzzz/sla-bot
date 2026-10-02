from __future__ import annotations

from collections.abc import Callable

from app.db.uow import UnitOfWork
from app.schemas.users import TelegramUserData, TelegramUserRead
from app.services.clock import Clock


class TelegramUserService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def upsert(self, data: TelegramUserData) -> TelegramUserRead:
        async with self._uow_factory() as uow:
            user = await uow.users.upsert(data, self._clock.now())
            result = TelegramUserRead.model_validate(user)
            await uow.commit()
        return result
