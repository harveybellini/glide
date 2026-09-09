"""Live-user routes that require a connected Google identity.

These complement the shared sample/live routes in ``demo.py``. Place search
uses the injected ``PlaceLookup`` with ephemeral storage so the user can
confirm a real starting address before any journey is planned.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from glide.api.deps import LiveUser, Principal, get_principal
from glide.domain.models import PlaceRef

router = APIRouter(prefix="/api", tags=["live"])


@router.get("/places/search", response_model=list[PlaceRef])
def search_places(
    request: Request,
    query: str,
    principal: Annotated[Principal, Depends(get_principal)],
) -> list[PlaceRef]:
    if not isinstance(principal, LiveUser):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Place search requires a connected Google Calendar account.",
        )
    lookup = request.app.state.place_search
    if lookup is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Place search is not configured on this server.",
        )
    query = query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a place query.",
        )
    return lookup.search(query=query, storage_allowed=False)
