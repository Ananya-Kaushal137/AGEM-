"""workflows router (Architecture §10.3). Endpoints arrive in Prompt 7."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
