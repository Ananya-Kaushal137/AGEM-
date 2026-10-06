"""Settings — the single entry point for every secret (Architecture §24, P16, FR-AUTH-004).

Nothing else in the codebase reads `os.environ`. Secrets are `SecretStr`, so a
stray `print(settings)` or a traceback shows `**********` instead of the value.
They are never stored in PostgreSQL and never echoed in an API response.
"""

import functools
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Read once, from the environment, populated by docker-compose from `.env`."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql://agem:agem@localhost:5432/agem"

    # FR-AUTH-001. Left empty, every request is rejected rather than let through.
    api_key: SecretStr = SecretStr("")

    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    llm_api_key: SecretStr = SecretStr("")
    # FR-DIAG-008. Empty means the provider default in app/core/llm.py:
    # cheap tier for diagnosis, strong tier for code generation.
    llm_model_cheap: str = ""
    llm_model_strong: str = ""
    # FR-AUTH-006: debug only. On, LLM prompts and replies (which carry agent payloads) are logged.
    llm_log_payloads: bool = False

    # FR-AUTH-005: encrypts Agent.encrypted_credentials at rest.
    fernet_key: SecretStr = SecretStr("")


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor, so the environment is read exactly once per process."""
    return Settings()
