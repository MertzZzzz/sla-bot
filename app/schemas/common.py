from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """Base for immutable DTOs; ``from_attributes`` allows building them from ORM rows."""

    model_config = ConfigDict(from_attributes=True, frozen=True)
