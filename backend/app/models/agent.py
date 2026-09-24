"""Agent and AgentCapability (Architecture §14.1, §15.4).

Agents are registered, never built by AGEM (FRS §1.3). Registration stores
endpoint/connection info only — no source-code upload in the MVP (FR-AGT-008).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow, uuid_pk
from .enums import AgentFramework, AgentStatus

if TYPE_CHECKING:
    from .capability import Capability
    from .execution import ExecutionStep
    from .user import User
    from .workflow import WorkflowAgent


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    agent_id: Mapped[uuid.UUID] = uuid_pk("agent_id")
    # RESTRICT: the single seeded user owns everything and is never deleted.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    framework: Mapped[AgentFramework] = mapped_column(
        SAEnum(
            AgentFramework,
            name="agent_framework",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # Endpoint / runtime connection info. Agents are reached only over HTTP
    # through an adapter; AGEM never imports agent code (FR-ADP-007).
    endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AgentStatus] = mapped_column(
        SAEnum(
            AgentStatus,
            name="agent_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=AgentStatus.ACTIVE,
    )
    # Fernet ciphertext, never plaintext, and never echoed in an API response
    # (FR-AGT-007, Architecture §18.2).
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="agents")
    # No cascade on either side: an agent referenced by a workflow cannot be
    # deleted, which is what produces the 409 in FR-AGT-006.
    workflow_agents: Mapped[list["WorkflowAgent"]] = relationship(
        back_populates="agent"
    )
    execution_steps: Mapped[list["ExecutionStep"]] = relationship(
        back_populates="agent"
    )
    capabilities: Mapped[list["AgentCapability"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.framework.value}) {self.status.value}>"


class AgentCapability(Base, TimestampMixin):
    """Join table recording which capabilities an agent has been granted (FR-AGT-009)."""

    __tablename__ = "agent_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "capability_id", name="uq_agent_capabilities_agent_capability"
        ),
    )

    agent_capability_id: Mapped[uuid.UUID] = uuid_pk("agent_capability_id")
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("capabilities.capability_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # Who or what granted it — "capability_engine" for an automatic grant after
    # verification, or a user identifier for a manual one.
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)

    agent: Mapped["Agent"] = relationship(back_populates="capabilities")
    capability: Mapped["Capability"] = relationship(back_populates="grants")

    def __repr__(self) -> str:
        return f"<AgentCapability agent={self.agent_id} capability={self.capability_id}>"
