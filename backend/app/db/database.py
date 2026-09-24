"""SQLAlchemy engine, session factory and the shared `Base`.

`Base` itself lives in `app/models/base.py` and is re-exported here so callers
have one obvious import for "the database".
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.base import Base

__all__ = ["Base", "engine", "SessionLocal", "get_db"]

engine = create_engine(
    get_settings().database_url,
    # Verify a pooled connection before handing it out, so a Postgres restart
    # doesn't surface as a stale-connection error mid-execution.
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
