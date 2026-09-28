"""The one base class every request/response schema inherits (FR-API-002, Architecture §10.1)."""

from pydantic import BaseModel, ConfigDict

__all__ = ["StrictModel"]


class StrictModel(BaseModel):
    """Unknown fields are rejected with 422, never silently ignored."""

    model_config = ConfigDict(extra="forbid")
