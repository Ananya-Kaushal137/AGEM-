"""Runs one execution of an already-validated DAG (Architecture §11.1-11.2, FR-ORC-001…014).

The Orchestrator does not plan. The workflow's run order was worked out once, at
creation, and stored in `step_order` (FR-WFL-004); here it is only followed:

    repeat:
        READY = PENDING steps whose depends_on are all SUCCEEDED   (in step_order)
        run the READY steps together under asyncio.gather          (FR-ORC-004)
    until nothing is READY

Every status change goes through `can_transition` (FR-ORC-009) and is committed
together with the execution roll-up and, on success, the checkpoint — one
transaction per transition (FR-ORC-013, P3). The database is the only state:
nothing about a run lives only in memory.

Not here yet (Roadmap Week 4 is "no failure yet"): pause, diagnosis, retry and
recovery. A failed step simply ends FAILED and the run stops — Prompt 11 replaces
that with PAUSED → `master_agent.diagnose_failure`.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models import Checkpoint, Execution, ExecutionStep, utcnow
from app.models.enums import ErrorType, ExecutionStatus, StepStatus, can_transition
from app.schemas.workflow import INPUT_SOURCE

from .step_executor import SUCCEEDED, StepExecutor, failure

__all__ = ["Orchestrator", "IllegalTransition", "rollup"]

logger = logging.getLogger("agem.orchestrator")


class IllegalTransition(RuntimeError):
    """A status write that `can_transition` refuses. Always a bug, never a normal outcome."""


def rollup(statuses: Iterable[StepStatus]) -> ExecutionStatus:
    """`Execution.status` from its steps (Architecture §15.3, FR-ORC-010) — never set any other way."""
    statuses = [StepStatus(s) for s in statuses]
    if any(s == StepStatus.PAUSED for s in statuses):
        return ExecutionStatus.PAUSED
    if any(s == StepStatus.FAILED for s in statuses):
        return ExecutionStatus.FAILED
    if statuses and all(s == StepStatus.SUCCEEDED for s in statuses):
        return ExecutionStatus.SUCCEEDED
    if all(s == StepStatus.PENDING for s in statuses):
        return ExecutionStatus.PENDING
    return ExecutionStatus.RUNNING


@dataclass(frozen=True)
class Plan:
    """The workflow's fixed shape, translated from WorkflowAgent ids to this run's step ids.

    Read once per run. It only describes the DAG; every status is read from the database.
    """

    order: list[UUID]                          # step ids in stored step_order
    depends_on: dict[UUID, list[UUID]]
    input_mapping: dict[UUID, dict[str, dict]]  # target field -> {"from": step id | "input", "field", "type"}
    keys: dict[UUID, str]                       # the workflow's step keys, for final_output
    sinks: list[UUID]                           # steps nothing depends on: their outputs are the result


class Orchestrator:
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal,
                 executor: StepExecutor | None = None) -> None:
        self._session = session_factory
        self._executor = executor or StepExecutor(session_factory)

    async def run_execution(self, execution_id: UUID) -> None:
        """Run every step of a PENDING execution (FR-ORC-002). Started by `BackgroundTasks`, so it never raises."""
        try:
            plan = self._plan(execution_id)
            if plan is None:
                return
            while ready := self._ready_steps(execution_id, plan):
                await asyncio.gather(*(self._run_one(plan, step_id) for step_id in ready))
        except Exception:  # noqa: BLE001 — a background task has no caller to raise to
            logger.exception("orchestrator crashed", extra={"agem": {"execution_id": str(execution_id)}})

    # --- reading -----------------------------------------------------------

    def _plan(self, execution_id: UUID) -> Plan | None:
        with self._session() as db:
            execution = db.get(Execution, execution_id)
            if execution is None:
                logger.warning("execution not found", extra={"agem": {"execution_id": str(execution_id)}})
                return None
            if execution.status != ExecutionStatus.PENDING:
                # Already started by another task: running it twice would call agents twice.
                return None
            return _plan_for(execution)

    def _ready_steps(self, execution_id: UUID, plan: Plan) -> list[UUID]:
        """FR-ORC-003: PENDING, and every step in depends_on SUCCEEDED. Stops once any step has FAILED."""
        with self._session() as db:
            status = dict(db.execute(
                select(ExecutionStep.step_id, ExecutionStep.status).where(ExecutionStep.execution_id == execution_id)
            ).all())
        if StepStatus.FAILED in status.values():
            return []
        return [s for s in plan.order
                if status[s] == StepStatus.PENDING
                and all(status[d] == StepStatus.SUCCEEDED for d in plan.depends_on[s])]

    # --- one step ------------------------------------------------------------

    async def _run_one(self, plan: Plan, step_id: UUID) -> None:
        started = time.perf_counter()
        with self._session() as db:
            step = db.get(ExecutionStep, step_id)
            if step.status != StepStatus.PENDING:  # another task got here first
                return
            step_input, missing = _build_input(db, step, plan)
            _move(step, StepStatus.RUNNING, plan, input=step_input, started_at=utcnow(),
                  attempts=step.attempts + 1)
            db.commit()
        bookkeeping = time.perf_counter() - started

        if missing:
            # The upstream output lacks a field this step's input_mapping names. Calling
            # the agent with half an input would only produce a confusing error later.
            result = failure(ErrorType.AGENT_ERROR, "input_mapping cannot be satisfied: " + "; ".join(missing))
        else:
            result = await self._executor.run_step(step_id)

        started = time.perf_counter()
        with self._session() as db:
            step = db.get(ExecutionStep, step_id)
            if result["status"] == SUCCEEDED:
                _move(step, StepStatus.SUCCEEDED, plan, output=result["output"], finished_at=utcnow())
                db.add(Checkpoint(step_id=step_id, state=_checkpoint_state(step, plan)))
            else:
                _move(step, StepStatus.FAILED, plan, error=result, finished_at=utcnow())
            db.commit()  # status, output, roll-up and checkpoint together (FR-ORC-013)
            bookkeeping += time.perf_counter() - started
            logger.info("step finished", extra={"agem": {
                "execution_id": str(step.execution_id), "step_id": str(step_id), "agent_id": str(step.agent_id),
                "status": step.status.value, "error_type": result.get("error_type"),
                "overhead_ms": round(bookkeeping * 1000, 1),  # FR-ORC-014: < 300 ms
            }})


def _plan_for(execution: Execution) -> Plan:
    """Match this run's steps to the workflow's steps by the stored step_order (unique per workflow)."""
    by_order = {s.step_order: s for s in execution.steps}
    wa_to_step = {wa.workflow_agent_id: by_order[wa.step_order].step_id for wa in execution.workflow.steps}
    keys = {s["workflow_agent_id"]: s["key"] for s in execution.workflow.definition.get("steps", [])}

    depends_on, mapping, names = {}, {}, {}
    for wa in execution.workflow.steps:
        sid = wa_to_step[wa.workflow_agent_id]
        depends_on[sid] = [wa_to_step[UUID(d)] for d in wa.depends_on]
        mapping[sid] = {
            target: {**src, "from": src["from"] if src["from"] == INPUT_SOURCE else wa_to_step[UUID(src["from"])]}
            for target, src in wa.input_mapping.items()
        }
        names[sid] = keys.get(str(wa.workflow_agent_id), f"step_{wa.step_order}")

    order = [s.step_id for s in sorted(execution.steps, key=lambda s: s.step_order)]
    upstream = {d for deps in depends_on.values() for d in deps}
    return Plan(order=order, depends_on=depends_on, input_mapping=mapping, keys=names,
                sinks=[s for s in order if s not in upstream])


def _build_input(db: Session, step: ExecutionStep, plan: Plan) -> tuple[dict[str, Any], list[str]]:
    """FR-ORC-005 / US-04: the step's input is exactly what its input_mapping declares.

    Upstream values are read from the upstream step's checkpoint, not recomputed.
    Returns the input and a list of fields that could not be found.
    """
    outputs: dict[UUID, dict] = {}
    step_input: dict[str, Any] = {}
    missing: list[str] = []
    for target, src in plan.input_mapping[step.step_id].items():
        if src["from"] == INPUT_SOURCE:
            source, where = step.execution.input or {}, "the execution input"
        else:
            upstream = src["from"]
            if upstream not in outputs:
                outputs[upstream] = _checkpointed_output(db, upstream)
            source, where = outputs[upstream], f'the output of step "{plan.keys[upstream]}"'
        if src["field"] in source:
            step_input[target] = source[src["field"]]
        else:
            missing.append(f'"{target}" needs field "{src["field"]}", which is not in {where}')
    return step_input, missing


def _checkpointed_output(db: Session, step_id: UUID) -> dict:
    checkpoint = db.scalars(
        select(Checkpoint).where(Checkpoint.step_id == step_id).order_by(Checkpoint.created_at.desc()).limit(1)
    ).first()
    return (checkpoint.state.get("outputs", {}).get(str(step_id)) or {}) if checkpoint else {}


def _move(step: ExecutionStep, new: StepStatus, plan: Plan, **fields: Any) -> None:
    """The only status write (FR-ORC-009). Re-rolls the execution in the same transaction (FR-ORC-010)."""
    if not can_transition(step.status, new):
        raise IllegalTransition(f"Step {step.step_id}: {step.status.value} -> {new.value} is not allowed.")
    step.status = new
    for name, value in fields.items():
        setattr(step, name, value)

    execution = step.execution
    status = rollup(s.status for s in execution.steps)
    if status == execution.status:
        return
    execution.status = status
    if status == ExecutionStatus.RUNNING and execution.started_at is None:
        execution.started_at = utcnow()
    if status == ExecutionStatus.SUCCEEDED:
        execution.finished_at = utcnow()
        outputs = {s.step_id: s.output for s in execution.steps}
        execution.final_output = (outputs[plan.sinks[0]] if len(plan.sinks) == 1
                                  else {plan.keys[s]: outputs[s] for s in plan.sinks})
    elif status == ExecutionStatus.FAILED:
        execution.finished_at = utcnow()
        error = step.error or {}
        execution.error_summary = (f'Step "{plan.keys[step.step_id]}" ({step.agent.name}) failed: '
                                   f'{error.get("error_type")}: {str(error.get("raw_error", ""))[:300]}')


def _checkpoint_state(step: ExecutionStep, plan: Plan) -> dict:
    """The success checkpoint, holding the five things Architecture §11.3 lists.

    Prompt 9 moves the writing of this into CheckpointManager.save and adds the pause checkpoint.
    """
    steps = step.execution.steps
    return {
        "current_step": str(step.step_id),
        "completed_steps": [str(s.step_id) for s in steps if s.status == StepStatus.SUCCEEDED],
        "outputs": {str(s.step_id): s.output for s in steps if s.status == StepStatus.SUCCEEDED},
        "pending_inputs": {str(s.step_id): s.input for s in steps
                           if s.status in (StepStatus.RUNNING, StepStatus.PAUSED)},
        "workflow_state": {
            "execution_id": str(step.execution_id),
            "workflow_id": str(step.execution.workflow_id),
            "execution_status": step.execution.status.value,
            "step_status": {str(s.step_id): s.status.value for s in steps},
            "step_keys": {str(s): k for s, k in plan.keys.items()},
        },
        "recovery": {"recovery_stage": step.recovery_stage.value, "attempts": step.attempts},
    }
