"""agents router (Architecture §10.3, FR-AGT-001…011)."""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adapters.rest_adapter import RestAdapter
from app.core.crypto import encrypt
from app.core.errors import AppError, ErrorCode
from app.db.database import get_db
from app.db.seed import OWNER_EMAIL, seed_owner
from app.models import Agent, User
from app.models.enums import AgentStatus
from app.schemas.agent import AgentCreate, AgentRead

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _read(agent: Agent) -> AgentRead:
    return AgentRead.model_validate(
        {**{c: getattr(agent, c) for c in AgentRead.model_fields if c != "has_credentials"},
         "has_credentials": agent.encrypted_credentials is not None}
    )


def _owner(db: Session) -> User:
    return db.scalars(select(User).where(User.email == OWNER_EMAIL)).one_or_none() or seed_owner(db)


def _get(db: Session, agent_id: uuid.UUID) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise AppError(ErrorCode.AGENT_NOT_FOUND, f"Agent {agent_id} not found.")
    return agent


@router.post("", status_code=201, response_model=AgentRead)
async def register_agent(body: AgentCreate, db: Session = Depends(get_db)) -> AgentRead:
    endpoint = str(body.endpoint).rstrip("/")
    credentials = body.credentials.get_secret_value() if body.credentials else None

    # FR-AGT-011 / ADR-009: only an agent that answers its own /health is saved.
    if not await RestAdapter(endpoint, credentials=credentials).health():
        raise AppError(
            ErrorCode.AGENT_UNREACHABLE,
            f"No 200 from GET {endpoint}/health. Check the endpoint and that the agent is running.",
            {"endpoint": endpoint},
        )

    agent = Agent(
        user_id=_owner(db).user_id,
        name=body.name,
        framework=body.framework,
        endpoint=endpoint,
        description=body.description,
        status=AgentStatus.ACTIVE,
        encrypted_credentials=encrypt(credentials) if credentials else None,
    )
    db.add(agent)
    db.commit()
    return _read(agent)


@router.get("", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)) -> list[AgentRead]:
    return [_read(a) for a in db.scalars(select(Agent).order_by(Agent.created_at))]


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: uuid.UUID, db: Session = Depends(get_db)) -> AgentRead:
    return _read(_get(db, agent_id))


@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    db.delete(_get(db, agent_id))
    try:
        db.commit()
    except IntegrityError:
        # The ON DELETE RESTRICT foreign key from workflow_agents decides this,
        # not application logic (FR-AGT-006).
        db.rollback()
        raise AppError(ErrorCode.AGENT_IN_USE, f"Agent {agent_id} is used by a workflow and cannot be deleted.",
                       {"agent_id": str(agent_id)})
    return Response(status_code=204)
