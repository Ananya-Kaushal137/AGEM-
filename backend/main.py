"""AGEM backend — FastAPI entry point.

One process holds the API layer, the Orchestrator, the Master Agent and the
Capability Engine (Architecture §8, P1). Only the sandbox is a separate image.
"""

from fastapi import Depends, FastAPI

from app.api import agents, capabilities, executions, workflows
from app.core.auth import get_api_key
from app.core.errors import register_error_handlers


def create_app() -> FastAPI:
    # Declared on the app, so every route — including ones added later — needs
    # the X-API-Key header (FR-AUTH-001).
    app = FastAPI(title="AGEM", version="0.1.0", dependencies=[Depends(get_api_key)])
    register_error_handlers(app)

    for module in (agents, workflows, executions, capabilities):
        app.include_router(module.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
