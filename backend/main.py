"""AGEM backend — FastAPI entry point.

One process holds the API layer, the Orchestrator, the Master Agent and the
Capability Engine (Architecture §8). Only the sandbox is a separate image.

Routers, the error envelope and X-API-Key auth are added in Prompt 3.
"""

from fastapi import FastAPI

app = FastAPI(title="AGEM", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by docker-compose and by run_dev.sh."""
    return {"status": "ok"}
