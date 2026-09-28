"""Static API-key auth (FR-AUTH-001, Architecture §18.4).

One dependency, applied globally in `main.py`. No OAuth, JWT, login or RBAC —
FR-AUTH-003 defers them explicitly.
"""

import secrets

from fastapi import Security
from fastapi.security import APIKeyHeader

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

__all__ = ["get_api_key"]

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    expected = get_settings().api_key.get_secret_value()
    # An unset API_KEY fails closed: no header value can match it.
    if not expected or api_key is None or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise AppError(ErrorCode.UNAUTHORIZED, "Missing or invalid X-API-Key header.")
    return api_key
