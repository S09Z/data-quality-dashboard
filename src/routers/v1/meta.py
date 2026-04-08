"""Meta / liveness routes — v1."""

from __future__ import annotations

from fastapi import APIRouter

from src.schemas import HealthResponse

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        200: {
            "content": {
                "application/json": {"example": {"status": "ok", "version": "0.1.0"}}
            }
        }
    },
)
def health() -> HealthResponse:
    """Liveness check."""
    return HealthResponse(status="ok", version="0.1.0")
