"""Agent registration schemas (FR-AGT-001, FR-AGT-002, FR-AGT-007, docs/api-spec.md)."""

import uuid
from datetime import datetime

from pydantic import AnyHttpUrl, ConfigDict, Field, SecretStr

from app.models.enums import AgentFramework, AgentStatus

from .base import StrictModel

__all__ = ["AgentCreate", "AgentRead"]


class AgentCreate(StrictModel):
    """Endpoint/connection info only — there is no field for source code (FR-AGT-008)."""

    name: str = Field(min_length=1, max_length=255)
    # An unknown framework is a 422 here, at registration, not at execution (FR-AGT-002).
    framework: AgentFramework
    endpoint: AnyHttpUrl
    description: str | None = None
    credentials: SecretStr | None = None


class AgentRead(StrictModel):
    """Never carries credentials, only whether some are stored (FR-AGT-007)."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: uuid.UUID
    name: str
    framework: AgentFramework
    endpoint: str
    description: str | None
    status: AgentStatus
    has_credentials: bool
    created_at: datetime
    updated_at: datetime
