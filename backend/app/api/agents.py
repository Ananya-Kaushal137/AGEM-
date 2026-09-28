"""agents router (Architecture §10.3). Endpoints arrive in Prompt 5."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/agents", tags=["agents"])
