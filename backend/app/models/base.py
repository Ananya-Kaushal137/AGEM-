"""Declarative base and the two conventions every table obeys (Architecture §14.2).

- Primary keys are application-generated UUIDs, not auto-increment integers, so
  a POST can return an id before the row is committed and the id is safe to put
  in a URL (FR-DAT-001).
- Every table carries `created_at` and `updated_at` in UTC (FR-DAT-002).

Both live here as helpers so no model can forget them.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """The metadata Alembic autogenerates against."""


def utcnow() -> datetime:
    """Timezone-aware UTC now. The only clock the models use."""
    return datetime.now(timezone.utc)


def uuid_pk(name: str) -> Mapped[uuid.UUID]:
    """An application-generated UUID primary key called `name` (FR-DAT-001)."""
    return mapped_column(
        name,
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """`created_at` / `updated_at`, both UTC, on every table (FR-DAT-002)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )
