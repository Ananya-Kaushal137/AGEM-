"""The error envelope and the error code catalogue (FR-API-003, FR-API-004, Architecture §10.2).

Every 4xx/5xx response AGEM sends has exactly one shape:

    {"error_code": "AGENT_NOT_FOUND", "message": "...", "details": {...}}

Code raises `AppError(ErrorCode.X, "...")`; the handlers registered here turn it,
and every error FastAPI or Starlette raises on its own, into that envelope.
"""

from enum import Enum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = ["ErrorCode", "AppError", "register_error_handlers"]


class ErrorCode(str, Enum):
    """The catalogue in Architecture §10.2 — the only codes AGEM raises itself."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    WORKFLOW_CYCLE_DETECTED = "WORKFLOW_CYCLE_DETECTED"
    UNAUTHORIZED = "UNAUTHORIZED"
    CAPABILITY_BUILD_FAILED = "CAPABILITY_BUILD_FAILED"
    OUTPUT_DRIFT = "OUTPUT_DRIFT"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AGENT_NOT_FOUND: 404,
    ErrorCode.WORKFLOW_NOT_FOUND: 404,
    ErrorCode.EXECUTION_NOT_FOUND: 404,
    ErrorCode.CAPABILITY_NOT_FOUND: 404,
    ErrorCode.WORKFLOW_CYCLE_DETECTED: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.CAPABILITY_BUILD_FAILED: 500,
    ErrorCode.OUTPUT_DRIFT: 500,
}


class AppError(Exception):
    """Raised anywhere in the backend; rendered as the envelope with the catalogue's status."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = HTTP_STATUS[code]


def _envelope(status_code: int, error_code: str, message: str, details: dict[str, Any] | None = None,
              headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error_code": error_code, "message": message, "details": details or {}},
        headers=headers,
    )


async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return _envelope(exc.status_code, exc.code.value, exc.message, exc.details)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic's raw errors carry the offending `input`; dropping it keeps a
    # rejected body's credentials out of the response (FR-AUTH-004).
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return _envelope(422, ErrorCode.VALIDATION_ERROR.value, "Request validation failed.", {"errors": errors})


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Framework-level errors (unknown route, wrong method) sit outside the §10.2
    # catalogue, so their code is the standard HTTP reason, e.g. NOT_FOUND.
    code = HTTPStatus(exc.status_code).name
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return _envelope(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def _unhandled_error(_: Request, __: Exception) -> JSONResponse:
    return _envelope(500, "INTERNAL_ERROR", "Internal server error.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
