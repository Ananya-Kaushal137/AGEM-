"""Workflow schemas (FR-WFL-001, 005, 007, 009).

A step is named by a client-chosen `key` (e.g. "research"), because database ids
do not exist yet when the request is sent. Keys are turned into
`workflow_agent_id`s when the workflow is saved.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.models.enums import WorkflowStatus

from .base import StrictModel

__all__ = [
    "INPUT_SOURCE",
    "FieldSource",
    "StepCreate",
    "WorkflowCreate",
    "WorkflowSummary",
    "WorkflowDetail",
]

# `from` value meaning "the execution's starting input", not an upstream step.
INPUT_SOURCE = "input"

FieldType = Literal["string", "number", "integer", "boolean", "object", "array", "any"]


class FieldSource(StrictModel):
    """Where one input field comes from (FR-WFL-005). `type` is what the output
    drift check verifies after a resume (FR-CAP-026)."""

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from", min_length=1)
    field: str = Field(min_length=1)
    type: FieldType = "any"


class StepCreate(StrictModel):
    key: str = Field(min_length=1, max_length=64)
    agent_id: uuid.UUID
    depends_on: list[str] = []
    # target input field → where its value comes from
    input_mapping: dict[str, FieldSource] = {}

    @model_validator(mode="after")
    def _check(self) -> "StepCreate":
        if self.key == INPUT_SOURCE:
            raise ValueError(f'"{INPUT_SOURCE}" is reserved and cannot be a step key')
        # Depending on itself is a cycle: left to the topological sort, so it
        # gets the same 400 WORKFLOW_CYCLE_DETECTED as any other loop.
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError(f'step "{self.key}" lists the same dependency twice')
        for target, src in self.input_mapping.items():
            # Only a direct dependency is guaranteed to have finished first.
            if src.source != INPUT_SOURCE and src.source not in self.depends_on:
                raise ValueError(
                    f'step "{self.key}": input "{target}" comes from "{src.source}", '
                    f'which is not in its depends_on (or "{INPUT_SOURCE}")'
                )
        return self


class WorkflowCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    steps: list[StepCreate] = Field(min_length=1, max_length=5)  # FR-WFL-009

    @model_validator(mode="after")
    def _check(self) -> "WorkflowCreate":
        keys = [s.key for s in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError("step keys must be unique")
        for step in self.steps:
            unknown = [d for d in step.depends_on if d not in keys]
            if unknown:
                raise ValueError(f'step "{step.key}" depends on unknown step(s): {unknown}')
        return self


class StepRead(StrictModel):
    workflow_agent_id: uuid.UUID
    key: str
    agent_id: uuid.UUID
    agent_name: str
    step_order: int
    depends_on: list[uuid.UUID]
    input_mapping: dict


class GraphNode(StrictModel):
    """Shaped for React Flow: `id`, `position`, `data` (FR-WFL-007)."""

    id: str
    position: dict[str, int]
    data: dict


class GraphEdge(StrictModel):
    id: str
    source: str
    target: str


class Graph(StrictModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class WorkflowSummary(StrictModel):
    workflow_id: uuid.UUID
    name: str
    status: WorkflowStatus
    step_count: int
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowSummary):
    steps: list[StepRead]
    graph: Graph
