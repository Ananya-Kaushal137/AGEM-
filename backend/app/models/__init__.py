"""SQLAlchemy models — the nine entities in Architecture §14.1.

Importing this package imports every model, so `Base.metadata` is complete.
Alembic's `env.py` relies on that for autogenerate.
"""

from .agent import Agent, AgentCapability
from .base import Base, TimestampMixin, utcnow
from .capability import Capability
from .enums import (
    TERMINAL_STEP_STATUSES,
    AgentFramework,
    AgentStatus,
    CapabilitySource,
    CapabilityStatus,
    ExecutionStatus,
    RecoveryStage,
    StepStatus,
    WorkflowStatus,
    can_transition,
)
from .execution import Checkpoint, Execution, ExecutionStep
from .user import User
from .workflow import Workflow, WorkflowAgent

__all__ = [
    # base
    "Base",
    "TimestampMixin",
    "utcnow",
    # the nine entities
    "User",
    "Agent",
    "Capability",
    "AgentCapability",
    "Workflow",
    "WorkflowAgent",
    "Execution",
    "ExecutionStep",
    "Checkpoint",
    # enums and the transition guard
    "StepStatus",
    "RecoveryStage",
    "ExecutionStatus",
    "AgentStatus",
    "WorkflowStatus",
    "CapabilityStatus",
    "AgentFramework",
    "CapabilitySource",
    "can_transition",
    "TERMINAL_STEP_STATUSES",
]
