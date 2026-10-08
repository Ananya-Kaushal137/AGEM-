"""workflows router (Architecture §10.3, FR-WFL-001…009).

The DAG is validated and sorted once, here, at creation (P10, BR-06). The result
is stored in `WorkflowAgent.step_order`; the Orchestrator never re-sorts.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, ErrorCode
from app.db.database import get_db
from app.db.seed import OWNER_EMAIL, seed_owner
from app.models import Agent, User, Workflow, WorkflowAgent
from app.models.enums import AgentStatus, WorkflowStatus
from app.schemas.workflow import INPUT_SOURCE, StepCreate, WorkflowCreate, WorkflowDetail, WorkflowSummary

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

X_GAP, Y_GAP = 250, 120


def topological_order(steps: list[StepCreate]) -> list[str]:
    """Kahn's algorithm. Returns step keys in run order, ties broken by request order.

    Raises WORKFLOW_CYCLE_DETECTED if some steps can never become ready (FR-WFL-002).
    """
    remaining = {s.key: set(s.depends_on) for s in steps}
    order: list[str] = []
    while remaining:
        ready = [s.key for s in steps if s.key in remaining and not remaining[s.key]]
        if not ready:
            raise AppError(
                ErrorCode.WORKFLOW_CYCLE_DETECTED,
                "The step dependencies contain a loop, so no valid run order exists.",
                {"steps_in_cycle": sorted(remaining)},
            )
        for key in ready:
            order.append(key)
            del remaining[key]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


def _owner(db: Session) -> User:
    return db.scalars(select(User).where(User.email == OWNER_EMAIL)).one_or_none() or seed_owner(db)


def _check_agents(db: Session, steps: list[StepCreate]) -> None:
    """FR-WFL-003 / FR-AGT-003: every agent must exist and be ACTIVE, checked now, not at run time."""
    wanted = {s.agent_id for s in steps}
    found = {a.agent_id: a for a in db.scalars(select(Agent).where(Agent.agent_id.in_(wanted)))}
    missing = sorted(str(i) for i in wanted - found.keys())
    if missing:
        raise AppError(ErrorCode.AGENT_NOT_FOUND, "Some steps use agents that are not registered.",
                       {"agent_ids": missing})
    inactive = sorted(str(a.agent_id) for a in found.values() if a.status != AgentStatus.ACTIVE)
    if inactive:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Some steps use INACTIVE agents.", {"agent_ids": inactive})


def _depth(wf: Workflow) -> dict[uuid.UUID, int]:
    depth: dict[uuid.UUID, int] = {}
    for step in wf.steps:  # already in step_order, so dependencies come first
        depth[step.workflow_agent_id] = 1 + max((depth[uuid.UUID(d)] for d in step.depends_on), default=-1)
    return depth


def _detail(wf: Workflow) -> WorkflowDetail:
    keys = {s["workflow_agent_id"]: s["key"] for s in wf.definition["steps"]}
    depth = _depth(wf)
    row: dict[int, int] = {}
    nodes, edges = [], []
    for step in wf.steps:
        sid = str(step.workflow_agent_id)
        level = depth[step.workflow_agent_id]
        row[level] = row.get(level, -1) + 1
        nodes.append({
            "id": sid,
            "position": {"x": level * X_GAP, "y": row[level] * Y_GAP},
            "data": {"label": step.agent.name, "key": keys[sid], "agent_id": str(step.agent_id),
                     "step_order": step.step_order},
        })
        edges += [{"id": f"{d}->{sid}", "source": d, "target": sid} for d in step.depends_on]

    return WorkflowDetail(
        workflow_id=wf.workflow_id, name=wf.name, status=wf.status, step_count=len(wf.steps),
        created_at=wf.created_at, updated_at=wf.updated_at,
        steps=[{
            "workflow_agent_id": s.workflow_agent_id, "key": keys[str(s.workflow_agent_id)],
            "agent_id": s.agent_id, "agent_name": s.agent.name, "step_order": s.step_order,
            "depends_on": s.depends_on, "input_mapping": s.input_mapping,
        } for s in wf.steps],
        graph={"nodes": nodes, "edges": edges},
    )


def _load(db: Session, workflow_id: uuid.UUID) -> Workflow:
    wf = db.scalars(
        select(Workflow).where(Workflow.workflow_id == workflow_id)
        .options(selectinload(Workflow.steps).selectinload(WorkflowAgent.agent))
    ).one_or_none()
    if wf is None:
        raise AppError(ErrorCode.WORKFLOW_NOT_FOUND, f"Workflow {workflow_id} not found.")
    return wf


@router.post("", status_code=201, response_model=WorkflowDetail)
def create_workflow(body: WorkflowCreate, db: Session = Depends(get_db)) -> WorkflowDetail:
    order = topological_order(body.steps)
    _check_agents(db, body.steps)

    ids = {s.key: uuid.uuid4() for s in body.steps}
    by_key = {s.key: s for s in body.steps}
    # Nothing is written until validation has passed, so an invalid workflow is
    # never saved; a saved one is therefore ACTIVE straight away (FR-WFL-006).
    wf = Workflow(
        user_id=_owner(db).user_id,
        name=body.name,
        status=WorkflowStatus.ACTIVE,
        definition={"steps": [{"workflow_agent_id": str(ids[s.key]), **s.model_dump(mode="json", by_alias=True)}
                              for s in body.steps]},
    )
    for position, key in enumerate(order, start=1):
        step = by_key[key]
        wf.steps.append(WorkflowAgent(
            workflow_agent_id=ids[key],
            agent_id=step.agent_id,
            step_order=position,
            depends_on=[str(ids[d]) for d in step.depends_on],
            input_mapping={
                target: {"from": src.source if src.source == INPUT_SOURCE else str(ids[src.source]),
                         "field": src.field, "type": src.type}
                for target, src in step.input_mapping.items()
            },
        ))
    db.add(wf)
    db.commit()
    return _detail(_load(db, wf.workflow_id))


@router.get("", response_model=list[WorkflowSummary])
def list_workflows(db: Session = Depends(get_db)) -> list[WorkflowSummary]:
    workflows = db.scalars(select(Workflow).options(selectinload(Workflow.steps)).order_by(Workflow.created_at))
    return [WorkflowSummary(workflow_id=w.workflow_id, name=w.name, status=w.status, step_count=len(w.steps),
                            created_at=w.created_at, updated_at=w.updated_at) for w in workflows]


@router.get("/{workflow_id}", response_model=WorkflowDetail)
def get_workflow(workflow_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowDetail:
    return _detail(_load(db, workflow_id))
