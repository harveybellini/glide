from __future__ import annotations

from fastapi import APIRouter, Request

from glide import __version__
from glide.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        mode="sample" if request.app.state.demo_store is not None else "unconfigured",
        version=__version__,
    )
