"""Every status enum in AGEM, defined once (Architecture §15, FR-DAT-003).

FRS §2.4 makes this file the single home for these values: they are mirrored in
`frontend/src/types/` and never hardcoded in a component or a handler.

`can_transition` is the whole of the step state machine. Architecture §11.5 and
FR-ORC-009 explicitly reject a State-pattern class hierarchy here — with five
states, one enum plus one function is the right size.
"""

from enum import Enum


class StepStatus(str, Enum):
    """`ExecutionStep.status` — exactly five values (Architecture §15.1)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class RecoveryStage(str, Enum):
    """`ExecutionStep.recovery_stage` — eight values (Architecture §15.2).

    Only meaningful while `status` is `PAUSED`. Kept separate from `StepStatus`
    precisely so the status enum can stay at five values; the Executions page
    renders this as the "capability gap in progress" node state (FR-ORC-011).
    """

    NONE = "NONE"
    DIAGNOSING = "DIAGNOSING"
    SEARCHING = "SEARCHING"
    BUILDING = "BUILDING"
    SANDBOXING = "SANDBOXING"
    TESTING = "TESTING"
    VERIFYING = "VERIFYING"
    REGISTERED = "REGISTERED"


class ExecutionStatus(str, Enum):
    """`Execution.status` — rolled up from its steps (Architecture §15.3).

    PAUSED if any step is paused, FAILED if any failed, SUCCEEDED only when
    every step succeeded. Never set independently of the steps (FR-ORC-010).
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentStatus(str, Enum):
    """`Agent.status` (Architecture §15.4).

    An `INACTIVE` agent cannot be added to a new workflow, but executions that
    already reference it still resolve — which is why deleting an agent returns
    409 rather than cascading (FR-AGT-003, FR-AGT-006).
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class WorkflowStatus(str, Enum):
    """`Workflow.status` — DRAFT → ACTIVE → ARCHIVED (Architecture §15.5).

    A workflow only reaches `ACTIVE` once its DAG passes validation, so a broken
    workflow can never reach the Orchestrator (FR-WFL-006).
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class CapabilityStatus(str, Enum):
    """`Capability.status` (Architecture §15.6).

    Only `VERIFIED` rows are handed to an agent or reused from the registry.
    `FAILED` rows are kept for the audit trail, never deleted, because "why was
    this tool rejected" is a viva question.
    """

    BUILDING = "BUILDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


class AgentFramework(str, Enum):
    """`Agent.framework` — validated at registration, not at execution (FR-AGT-002)."""

    PYTHON = "python"
    REST = "rest"
    LANGCHAIN = "langchain"
    CREWAI = "crewai"


class CapabilitySource(str, Enum):
    """`Capability.source` — how the capability was obtained.

    `ACQUIRED` when a free tool from `Searcher.research` covered the gap, `BUILT` when the
    Builder generated it. Surfaced on the Capabilities page (FR-UI-009).
    """

    BUILT = "BUILT"
    ACQUIRED = "ACQUIRED"


# --- Step state machine (Architecture §15.1) --------------------------------
#
#   [*] --> PENDING
#   PENDING --> RUNNING
#   RUNNING --> SUCCEEDED | FAILED | PAUSED
#   PAUSED  --> RUNNING          (resumed)
#   PAUSED  --> FAILED           (3 attempts exhausted)
#   SUCCEEDED, FAILED are terminal.
#
# Anything not listed here is illegal, including self-transitions and any move
# out of a terminal state.
_LEGAL_STEP_TRANSITIONS: frozenset[tuple[StepStatus, StepStatus]] = frozenset(
    {
        (StepStatus.PENDING, StepStatus.RUNNING),
        (StepStatus.RUNNING, StepStatus.SUCCEEDED),
        (StepStatus.RUNNING, StepStatus.FAILED),
        (StepStatus.RUNNING, StepStatus.PAUSED),
        (StepStatus.PAUSED, StepStatus.RUNNING),
        (StepStatus.PAUSED, StepStatus.FAILED),
    }
)

TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED}
)


def can_transition(old: StepStatus, new: StepStatus) -> bool:
    """Return True only if `old -> new` is a legal `ExecutionStep.status` move.

    The single guard required by FR-ORC-009. Every status write in
    `orchestrator/` goes through this; nothing else decides legality.

    Accepts plain strings too, since values round-trip from the database that
    way.

    >>> can_transition(StepStatus.PENDING, StepStatus.RUNNING)
    True
    >>> can_transition(StepStatus.SUCCEEDED, StepStatus.RUNNING)
    False
    """
    return (StepStatus(old), StepStatus(new)) in _LEGAL_STEP_TRANSITIONS
