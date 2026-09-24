"""User (Architecture §14.1).

The MVP has no login — FR-AUTH-001 is a single static API key — so exactly one
seeded row owns every agent, workflow and execution (FR-AUTH-002). The table
exists so the ownership foreign keys are real rather than implied, and so adding
real accounts later is a migration rather than a redesign.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from .agent import Agent
    from .execution import Execution
    from .workflow import Workflow


class User(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = uuid_pk("user_id")
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # The static API_KEY is never stored in plaintext; only its hash lives here.
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    agents: Mapped[list["Agent"]] = relationship(back_populates="user")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="user")
    executions: Mapped[list["Execution"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User {self.email}>"
