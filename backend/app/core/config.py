"""Settings — the single entry point for every secret (Architecture §24, P16).

Prompt 3 extends this with `API_KEY`, the LLM provider key and the Fernet key
(FR-AUTH-004). For now it holds only what the data layer needs, so that even the
database URL is read in one place rather than scattered through modules.
"""

import functools
import os

__all__ = ["Settings", "get_settings"]


class Settings:
    """Read once, from the environment, populated by docker-compose from `.env`."""

    def __init__(self) -> None:
        self.database_url: str = os.environ.get(
            "DATABASE_URL", "postgresql://agem:agem@localhost:5432/agem"
        )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor, so the environment is read exactly once per process."""
    return Settings()
