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

Pause and resume (Prompt 9, FR-CKP-002…008): a failed step is PAUSED with a
pause checkpoint; only that step, upstream steps are untouched. Independent
siblings keep running. `resume_step` re-runs exactly that step from its pause
checkpoint and then carries on. After MAX_ATTEMPTS failed attempts the step is
FAILED instead. There is no public resume endpoint (FR-CKP-007, BR-07):
`resume_step` is only called by AGEM itself, after a diagnosis (Prompt 11) or a
verified capability (Prompt 14).
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
from app.models import Execution, ExecutionStep, utcnow
from app.models.enums import ErrorType, ExecutionStatus, StepStatus, can_transition
from app.schemas.workflow import INPUT_SOURCE

from .checkpoint_manager import CheckpointManager, build_state
from .step_executor import SUCCEEDED, StepExecutor, failure

__all__ = ["Orchestrator", "IllegalTransition", "rollup", "MAX_ATTEMPTS"]

# FR-CKP-008: after this many failed attempts a PAUSED step becomes FAILED.
MAX_ATTEMPTS = 3

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
            await self._drive(execution_id, plan)
        except Exception:  # noqa: BLE001 — a background task has no caller to raise to
            logger.exception("orchestrator crashed", extra={"agem": {"execution_id": str(execution_id)}})

    async def resume_step(self, step_id: UUID, tool_results: dict | None = None) -> None:
        """Re-run exactly one PAUSED step from its pause checkpoint, then carry on with the run.

        Internal only (FR-CKP-007): called after a diagnosis or a verified capability,
        never by a user. `tool_results` reaches the agent as `context.tool_results`.
        A step that is not PAUSED (already SUCCEEDED, or being resumed right now) is
        left alone, so a repeated call can't duplicate work (FR-CKP-006). Never raises.
        """
        try:
            started = time.perf_counter()
            with self._session() as db:
                step = db.get(ExecutionStep, step_id)
                if step is None or step.status != StepStatus.PAUSED:
                    logger.info("resume skipped", extra={"agem": {
                        "step_id": str(step_id), "status": step.status.value if step else None}})
                    return
                plan = _plan_for(step.execution)
                if step.attempts >= MAX_ATTEMPTS:
                    _move(step, StepStatus.FAILED, plan, finished_at=utcnow())
                    db.commit()
                    return
                # FR-CKP-005: the input saved at the pause, not rebuilt from upstream steps.
                step_input = CheckpointManager(db).paused_input(step_id)
                if step_input is None:
                    step_input = step.input or {}
                _move(step, StepStatus.RUNNING, plan, input=step_input, attempts=step.attempts + 1)
                db.commit()
                execution_id = step.execution_id
            context = {"tool_results": tool_results} if tool_results else {}
            result = await self._executor.run_step(step_id, context=context)
            self._finish(plan, step_id, result, time.perf_counter() - started)
            await self._drive(execution_id, plan)
        except Exception:  # noqa: BLE001 — called from background work, nobody to raise to
            logger.exception("resume crashed", extra={"agem": {"step_id": str(step_id)}})

    async def _drive(self, execution_id: UUID, plan: "Plan") -> None:
        while ready := self._ready_steps(execution_id, plan):
            await asyncio.gather(*(self._run_one(plan, step_id) for step_id in ready))

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
        """FR-ORC-003: PENDING, and every step in depends_on SUCCEEDED. Stops once any step has FAILED.

        A PAUSED step does not stop the run: its dependants simply never become
        READY, while independent siblings carry on (Architecture §11.2).
        """
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
        self._finish(plan, step_id, result, bookkeeping)

    def _finish(self, plan: "Plan", step_id: UUID, result: dict, bookkeeping: float) -> None:
        """Record one attempt: SUCCEEDED + success checkpoint, or PAUSED + pause checkpoint.

        A failure on the last allowed attempt still writes the pause checkpoint, then
        moves PAUSED → FAILED (FR-CKP-008), so the failed input stays inspectable.
        """
        started = time.perf_counter()
        with self._session() as db:
            step = db.get(ExecutionStep, step_id)
            checkpoints = CheckpointManager(db)
            if result["status"] == SUCCEEDED:
                _move(step, StepStatus.SUCCEEDED, plan, output=result["output"], finished_at=utcnow())
                checkpoints.save(step_id, _checkpoint_state(step, plan))
            else:
                # FR-CKP-004: only this step pauses; upstream steps are not touched.
                _move(step, StepStatus.PAUSED, plan, error=result)
                checkpoints.save(step_id, _checkpoint_state(step, plan, error=result))
                if step.attempts >= MAX_ATTEMPTS:
                    _move(step, StepStatus.FAILED, plan, finished_at=utcnow())
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
                outputs[upstream] = CheckpointManager(db).output_of(upstream)
            source, where = outputs[upstream], f'the output of step "{plan.keys[upstream]}"'
        if src["field"] in source:
            step_input[target] = source[src["field"]]
        else:
            missing.append(f'"{target}" needs field "{src["field"]}", which is not in {where}')
    return step_input, missing


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


def _checkpoint_state(step: ExecutionStep, plan: Plan, error: dict | None = None) -> dict:
    return build_state(step, plan.keys, error)
