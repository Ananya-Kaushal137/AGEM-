"""Execution schemas (FR-ORC-001)."""

import uuid
from typing import Any

from pydantic import Field

from app.models.enums import ExecutionStatus

from .base import StrictModel

__all__ = ["ExecutionCreate", "ExecutionStarted"]


class ExecutionCreate(StrictModel):
    # Sent to every agent as `task` (docs/api-spec.md, agent contract).
    task: str = Field(min_length=1, max_length=10_000)
    # The starting values; a step reads them through `"from": "input"` in its input_mapping.
    input: dict[str, Any] = {}


class ExecutionStarted(StrictModel):
    """Returned at once; the run itself happens in the background (P2)."""

    execution_id: uuid.UUID
    status: ExecutionStatus
