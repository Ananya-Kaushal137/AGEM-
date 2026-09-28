"""Seed the single User row that owns everything (FR-AUTH-002).

Run once after `alembic upgrade head`:

    python -m app.db.seed

Safe to run again: it never creates a second user.
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User

__all__ = ["OWNER_EMAIL", "seed_owner"]

OWNER_EMAIL = "owner@agem.local"


def seed_owner(db: Session) -> User:
    api_key = get_settings().api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("API_KEY is not set; set it in .env before seeding.")
    # API_KEY is a long random token, so a fast hash is enough; a slow password
    # hash (bcrypt) only matters for low-entropy human passwords.
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    owner = db.scalars(select(User).where(User.email == OWNER_EMAIL)).one_or_none()
    if owner is None:
        owner = User(email=OWNER_EMAIL, api_key_hash=key_hash)
        db.add(owner)
    elif owner.api_key_hash != key_hash:
        owner.api_key_hash = key_hash
    db.commit()
    return owner


if __name__ == "__main__":
    from app.db.database import SessionLocal

    with SessionLocal() as session:
        user = seed_owner(session)
        print(f"owner user ready: {user.email} ({user.user_id})")
