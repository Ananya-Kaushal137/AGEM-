"""Saves and loads execution checkpoint state (Architecture §11.3, FR-CKP-001…005).

A checkpoint is written twice per step: when it succeeds, and the moment it pauses
(FR-CKP-002). `save` only adds the row to the caller's session; the caller commits
it together with the step's status change, so a step is never SUCCEEDED or PAUSED
without its checkpoint (FR-ORC-013).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Checkpoint, ExecutionStep
from app.models.enums import StepStatus

__all__ = ["CheckpointManager", "build_state"]

KEYS = ("current_step", "completed_steps", "outputs", "pending_inputs", "workflow_state", "recovery")


def build_state(step: ExecutionStep, step_keys: dict[UUID, str], error: dict | None = None) -> dict[str, Any]:
    """The five things Architecture §11.3 lists, as of now (FR-CKP-003).

    For a pause, `error` is the normalised failure and goes under `recovery`, with
    the step's input under `pending_inputs` — what a resume needs and nothing else.
    """
    steps = step.execution.steps
    recovery: dict[str, Any] = {"recovery_stage": step.recovery_stage.value, "attempts": step.attempts}
    if error is not None:
        recovery["error"] = error
    return {
        "current_step": str(step.step_id),
        "completed_steps": [str(s.step_id) for s in steps if s.status == StepStatus.SUCCEEDED],
        "outputs": {str(s.step_id): s.output for s in steps if s.status == StepStatus.SUCCEEDED},
        "pending_inputs": {str(s.step_id): s.input for s in steps
                           if s.status in (StepStatus.RUNNING, StepStatus.PAUSED)},
        "workflow_state": {
            "execution_id": str(step.execution_id),
            "workflow_id": str(step.execution.workflow_id),
            "task": step.execution.task,
            "execution_status": step.execution.status.value,
            "step_status": {str(s.step_id): s.status.value for s in steps},
            "step_keys": {str(s): k for s, k in step_keys.items()},
        },
        "recovery": recovery,
    }


class CheckpointManager:
    def __init__(self, db: Session) -> None:
        self._db = db

    def save(self, step_id: UUID, state: dict) -> None:
        """Add a checkpoint to the session (FR-CKP-001). The caller commits it with the status change."""
        missing = [k for k in KEYS if k not in state]
        if missing:
            raise ValueError(f"Checkpoint state is missing {missing} (FR-CKP-003).")
        self._db.add(Checkpoint(step_id=step_id, state=state))

    def load(self, execution_id: UUID) -> dict:
        """The newest checkpoint of the execution, or {} if none was written yet (FR-CKP-001)."""
        latest = self._db.scalars(
            select(Checkpoint).join(ExecutionStep).where(ExecutionStep.execution_id == execution_id)
            .order_by(Checkpoint.created_at.desc()).limit(1)
        ).first()
        return latest.state if latest else {}

    def latest_for_step(self, step_id: UUID) -> dict:
        latest = self._db.scalars(
            select(Checkpoint).where(Checkpoint.step_id == step_id).order_by(Checkpoint.created_at.desc()).limit(1)
        ).first()
        return latest.state if latest else {}

    def output_of(self, step_id: UUID) -> dict:
        """A finished upstream step's output, read from its checkpoint, never recomputed (FR-CKP-005)."""
        return self.latest_for_step(step_id).get("outputs", {}).get(str(step_id)) or {}

    def paused_input(self, step_id: UUID) -> dict | None:
        """The input saved when the step paused, or None if it has no pause checkpoint."""
        state = self.latest_for_step(step_id)
        if state.get("workflow_state", {}).get("step_status", {}).get(str(step_id)) != StepStatus.PAUSED.value:
            return None
        return state["pending_inputs"].get(str(step_id))
