"""Workflow and WorkflowAgent (Architecture §14.1, §15.5).

The topological sort runs once, at creation, and its result is stored on
`WorkflowAgent.step_order` — the Orchestrator never re-sorts at run time
(FR-WFL-004).
"""

import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, uuid_pk
from .enums import WorkflowStatus

if TYPE_CHECKING:
    from .agent import Agent
    from .execution import Execution
    from .user import User


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    workflow_id: Mapped[uuid.UUID] = uuid_pk("workflow_id")
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The DAG definition as JSONB rather than a separate table (FR-DAT-004).
    # Returned by the detail endpoint and consumed directly by React Flow
    # (FR-WFL-007).
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # DRAFT until the DAG passes validation; only then ACTIVE (FR-WFL-006), so
    # a broken workflow can never reach the Orchestrator.
    status: Mapped[WorkflowStatus] = mapped_column(
        SAEnum(
            WorkflowStatus,
            name="workflow_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=WorkflowStatus.DRAFT,
    )

    user: Mapped["User"] = relationship(back_populates="workflows")
    steps: Mapped[list["WorkflowAgent"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkflowAgent.step_order",
    )
    # No cascade: execution history outlives the workflow definition.
    executions: Mapped[list["Execution"]] = relationship(back_populates="workflow")

    def __repr__(self) -> str:
        return f"<Workflow {self.name} {self.status.value}>"


class WorkflowAgent(Base, TimestampMixin):
    """One agent's place, dependency and input mapping inside a workflow."""

    __tablename__ = "workflow_agents"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "step_order", name="uq_workflow_agents_workflow_step_order"
        ),
    )

    workflow_agent_id: Mapped[uuid.UUID] = uuid_pk("workflow_agent_id")
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ON DELETE RESTRICT, required by FR-DAT-005: this is exactly what makes
    # DELETE /api/agents/{id} return 409 instead of cascading (FR-AGT-006).
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # The stored result of the one topological sort (FR-WFL-004).
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # The workflow_agent_ids this step waits on. JSONB, not a join table:
    # 1-5 agents per workflow (FR-WFL-009) does not justify one.
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Declares which upstream output field feeds this step's input (FR-WFL-005).
    input_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    workflow: Mapped["Workflow"] = relationship(back_populates="steps")
    agent: Mapped["Agent"] = relationship(back_populates="workflow_agents")

    def __repr__(self) -> str:
        return f"<WorkflowAgent step={self.step_order} agent={self.agent_id}>"
