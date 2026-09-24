"""Execution, ExecutionStep and Checkpoint (Architecture §14.1, §15.1-15.3).

This is the CASCADE chain named in FR-DAT-005: deleting an Execution removes its
steps, and deleting a step removes its checkpoints. Nothing else in the schema
cascades.

The database is the single source of truth for a run — no execution state lives
only in memory (FR-DAT-008), which is what makes a mid-run backend restart
recoverable rather than lost (FR-CKP-009).
"""

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, uuid_pk
from .enums import ExecutionStatus, RecoveryStage, StepStatus

if TYPE_CHECKING:
    from .agent import Agent
    from .user import User
    from .workflow import Workflow


class Execution(Base, TimestampMixin):
    """One run of one workflow."""

    __tablename__ = "executions"

    execution_id: Mapped[uuid.UUID] = uuid_pk("execution_id")
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Rolled up from the steps, never set independently (FR-ORC-010).
    status: Mapped[ExecutionStatus] = mapped_column(
        SAEnum(
            ExecutionStatus,
            name="execution_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ExecutionStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow: Mapped["Workflow"] = relationship(back_populates="executions")
    user: Mapped["User"] = relationship(back_populates="executions")
    steps: Mapped[list["ExecutionStep"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ExecutionStep.step_order",
    )

    def __repr__(self) -> str:
        return f"<Execution {self.execution_id} {self.status.value}>"


class ExecutionStep(Base, TimestampMixin):
    """One run of one step inside an execution.

    `status` is the five-value machine in Architecture §15.1, guarded by
    `can_transition`. `recovery_stage` is a separate sub-state that is only
    meaningful while `status` is PAUSED — keeping them apart is what lets the
    status enum stay at five values (FR-ORC-011).
    """

    __tablename__ = "execution_steps"

    step_id: Mapped[uuid.UUID] = uuid_pk("step_id")
    # CASCADE, per FR-DAT-005.
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("executions.execution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT, matching the Agent policy: an execution that already references
    # an agent must still resolve (Architecture §15.4).
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Copied from WorkflowAgent.step_order at execution start, so a later edit
    # to the workflow cannot reorder a run already in progress.
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[StepStatus] = mapped_column(
        SAEnum(
            StepStatus,
            name="step_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=StepStatus.PENDING,
    )
    recovery_stage: Mapped[RecoveryStage] = mapped_column(
        SAEnum(
            RecoveryStage,
            name="recovery_stage",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RecoveryStage.NONE,
    )
    # The normalised error shape from FR-DIAG-002, stored whole.
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Bounds the pause: at 3 a PAUSED step moves to FAILED rather than pausing
    # forever (FR-CKP-008, FR-CAP-012).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    execution: Mapped["Execution"] = relationship(back_populates="steps")
    agent: Mapped["Agent"] = relationship(back_populates="execution_steps")
    # The step's "checkpoint/state reference" from Architecture §14.1.
    checkpoints: Mapped[list["Checkpoint"]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Checkpoint.created_at",
    )

    def __repr__(self) -> str:
        return f"<ExecutionStep {self.step_order} {self.status.value}>"


class Checkpoint(Base, TimestampMixin):
    """A resumable snapshot, written when a step succeeds and again when it pauses.

    Written twice per step, never only at the end of a run (FR-CKP-002), which
    is what makes exact-step resume possible without recomputing upstream steps
    (FR-CKP-005).
    """

    __tablename__ = "checkpoints"

    checkpoint_id: Mapped[uuid.UUID] = uuid_pk("checkpoint_id")
    # CASCADE, closing the Execution -> ExecutionStep -> Checkpoint chain in
    # FR-DAT-005.
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_steps.step_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # One JSONB blob, matching CheckpointManager.save(step_id, state).
    # FR-CKP-003 fixes its contents: current step, prior successful outputs,
    # pending inputs, workflow state and recovery information.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    step: Mapped["ExecutionStep"] = relationship(back_populates="checkpoints")

    def __repr__(self) -> str:
        return f"<Checkpoint {self.checkpoint_id} step={self.step_id}>"
