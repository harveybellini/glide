from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from glide.adapters.interfaces import StateStore
from glide.api.auth import AuthService
from glide.api.demo_store import DemoSession, DemoSessionStore
from glide.domain.models import UserSettings
from glide.jobs.queue import JobQueue


def get_demo_store(request: Request) -> DemoSessionStore:
    return request.app.state.demo_store


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


def get_state_store(request: Request) -> StateStore:
    return request.app.state.state_store


@dataclass
class LiveUser:
    """Authenticated Google identity for live routes."""

    user_id: str
    email: str
    settings: UserSettings


Principal = DemoSession | LiveUser


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_demo_session(
    session_id: Annotated[str, Header(alias="X-Glide-Session")],
    store: Annotated[DemoSessionStore, Depends(get_demo_store)],
) -> DemoSession:
    try:
        return store.get(session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sample session not found or expired. Create a new session first.",
        ) from exc


def get_principal(
    request: Request,
    session_id: Annotated[str | None, Header(alias="X-Glide-Session")] = None,
    demo_store: Annotated[DemoSessionStore, Depends(get_demo_store)] = None,
    state_store: Annotated[StateStore, Depends(get_state_store)] = None,
) -> Principal:
    """Resolve the request identity: sample session header first, then Google.

    A sample header pins the request to a synthetic tenant and never touches
    live credentials. Without a header, the encrypted Google session cookie
    identifies a live user whose settings must already exist.
    """

    if session_id:
        try:
            return demo_store.get(session_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sample session not found or expired. Create a new session first.",
            ) from exc
    auth_service: AuthService = request.app.state.auth_service
    current = auth_service.cookies.read(request)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Connect Google Calendar or start a sample session first.",
        )
    settings = state_store.get_settings(current.user_id)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Live settings are missing; disconnect and reconnect Google Calendar.",
        )
    return LiveUser(user_id=current.user_id, email=current.email, settings=settings)
