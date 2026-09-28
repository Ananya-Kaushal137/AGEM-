"""capabilities router (Architecture §10.3). Endpoints are added by a later prompt."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])
