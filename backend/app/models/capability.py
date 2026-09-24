"""Capability — the registry (Architecture §14.1, §15.6).

Every verified capability is registered here so a later gap of the same kind is
reused rather than rebuilt (BR-08, FR-CAP-016, ADR-006).
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, uuid_pk
from .enums import CapabilitySource, CapabilityStatus

if TYPE_CHECKING:
    from .agent import AgentCapability


class Capability(Base, TimestampMixin):
    __tablename__ = "capabilities"
    # The registry lookup key. Dedup is enforced here rather than in application
    # code, so two concurrent gap resolutions cannot register the same tool.
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_capabilities_name_version"),
    )

    capability_id: Mapped[uuid.UUID] = uuid_pk("capability_id")
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    source: Mapped[CapabilitySource] = mapped_column(
        SAEnum(
            CapabilitySource,
            name="capability_source",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    status: Mapped[CapabilityStatus] = mapped_column(
        SAEnum(
            CapabilityStatus,
            name="capability_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CapabilityStatus.BUILDING,
    )
    # Set by Verifier.verify() (FR-CAP-009). Null until verification runs.
    verification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The implementation reference: generated Python source when source is
    # BUILT, the tool reference when ACQUIRED. Read by the code viewer
    # (FR-CAP-020); never executed outside the sandbox.
    implementation: Mapped[str | None] = mapped_column(Text, nullable=True)

    grants: Mapped[list["AgentCapability"]] = relationship(
        back_populates="capability",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Capability {self.name}@{self.version} {self.status.value}>"
