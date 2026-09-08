from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from glide.adapters.interfaces import StateStore
from glide.api.demo_store import DemoSession, DemoSessionStore
from glide.jobs.queue import JobQueue


def get_demo_store(request: Request) -> DemoSessionStore:
    return request.app.state.demo_store


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


def get_state_store(request: Request) -> StateStore:
    return request.app.state.state_store


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
