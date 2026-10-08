"""executions router (Architecture §10.3, FR-ORC-001).

Starting a run only writes the PENDING rows and hands the execution to the
Orchestrator through `BackgroundTasks`, so the request returns at once (P2).
The status endpoints (`GET /api/executions/...`) arrive with the Executions page.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError, ErrorCode
from app.db.database import get_db
from app.models import Execution, ExecutionStep, Workflow
from app.models.enums import ExecutionStatus, StepStatus, WorkflowStatus
from app.schemas.execution import ExecutionCreate, ExecutionStarted
from orchestrator.orchestrator import Orchestrator

router = APIRouter(tags=["executions"])


def create_execution(db: Session, workflow: Workflow, body: ExecutionCreate) -> Execution:
    """One PENDING Execution plus one PENDING ExecutionStep per workflow step, committed.

    `step_order` is copied from the workflow, so the run follows the order fixed
    at creation (FR-WFL-004) even if the workflow changes later.
    """
    execution = Execution(workflow_id=workflow.workflow_id, user_id=workflow.user_id,
                          status=ExecutionStatus.PENDING, task=body.task, input=body.input)
    for wa in workflow.steps:
        execution.steps.append(ExecutionStep(agent_id=wa.agent_id, step_order=wa.step_order,
                                             status=StepStatus.PENDING))
    db.add(execution)
    db.commit()
    return execution


@router.post("/api/workflows/{workflow_id}/executions", status_code=202, response_model=ExecutionStarted)
def start_execution(workflow_id: uuid.UUID, body: ExecutionCreate, background: BackgroundTasks,
                    db: Session = Depends(get_db)) -> ExecutionStarted:
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise AppError(ErrorCode.WORKFLOW_NOT_FOUND, f"Workflow {workflow_id} not found.")
    if workflow.status != WorkflowStatus.ACTIVE:
        # Only a validated workflow may reach the Orchestrator (P10, FR-WFL-006).
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Workflow is {workflow.status.value}; only ACTIVE workflows run.",
                       {"status": workflow.status.value})

    execution = create_execution(db, workflow, body)
    # The run outlives this request's session, so the Orchestrator opens its own
    # sessions on the same database.
    sessions = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    background.add_task(Orchestrator(sessions).run_execution, execution.execution_id)
    return ExecutionStarted(execution_id=execution.execution_id, status=execution.status)
