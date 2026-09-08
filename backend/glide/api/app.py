from __future__ import annotations

import base64
import hashlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from glide import __version__
from glide.adapters.interfaces import StateStore
from glide.adapters.sqlite import SqliteStateStore
from glide.agent.strands_runner import build_agent_runner
from glide.api.auth import (
    AuthService,
    ExpiringStateStore,
    GoogleOAuthConfig,
    GoogleOAuthProvider,
    SessionCipher,
    SessionCookie,
    UnavailableOAuthProvider,
    create_auth_router,
)
from glide.api.demo_store import DemoSessionStore
from glide.api.routes import demo, health
from glide.api.run_service import build_run_processor
from glide.jobs.dispatcher import LocalDispatcher
from glide.jobs.queue import InMemoryJobQueue, JobQueue
from glide.jobs.worker import LocalWorker


def _worker_poll_interval() -> float:
    raw = os.getenv("GLIDE_WORKER_POLL_INTERVAL", "1.0")
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value > 0 else 1.0


def _schedule_interval() -> float:
    raw = os.getenv("GLIDE_SCHEDULE_INTERVAL", "300.0")
    try:
        value = float(raw)
    except ValueError:
        return 300.0
    return value if value >= 0 else 300.0


def create_app(
    *,
    state_store: StateStore | None = None,
    job_queue: JobQueue | None = None,
    run_local_worker: bool = True,
    schedule_interval: float | None = None,
) -> FastAPI:
    if state_store is None:
        state_store = SqliteStateStore(os.getenv("GLIDE_LOCAL_DB", "glide-local.db"))
    demo_store = DemoSessionStore(
        agent_runner=build_agent_runner(),
        state_store=state_store,
    )
    queue = job_queue if job_queue is not None else InMemoryJobQueue()
    worker: LocalWorker | None = None
    if run_local_worker and isinstance(queue, InMemoryJobQueue):
        worker = LocalWorker(
            queue=queue,
            processor=build_run_processor(demo_store, state_store),
            poll_interval_seconds=_worker_poll_interval(),
        )
    interval = (
        _schedule_interval() if schedule_interval is None else schedule_interval
    )
    dispatcher: LocalDispatcher | None = None
    if run_local_worker and interval > 0:
        dispatcher = LocalDispatcher(
            demo_store=demo_store,
            state_store=state_store,
            queue=queue,
            poll_interval_seconds=interval,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if worker is not None:
            worker.start()
        if dispatcher is not None:
            dispatcher.start()
        try:
            yield
        finally:
            if dispatcher is not None:
                dispatcher.stop()
            if worker is not None:
                worker.stop()

    app = FastAPI(
        title="Glide",
        version=__version__,
        description="A calendar agent that reserves time to travel.",
        lifespan=lifespan,
    )
    app.state.demo_store = demo_store
    app.state.state_store = state_store
    app.state.queue = queue
    app.state.worker = worker
    app.state.dispatcher = dispatcher

    try:
        config = GoogleOAuthConfig.from_env()
        provider = GoogleOAuthProvider(config)
        secure_cookies = config.secure_cookies
    except ValueError:
        provider = UnavailableOAuthProvider()
        secure_cookies = False

    session_secret = os.getenv("GLIDE_SESSION_SECRET")
    cipher_key = (
        base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())
        if session_secret
        else None
    )
    auth_service = AuthService(
        provider=provider,
        states=ExpiringStateStore(),
        cookies=SessionCookie(SessionCipher(cipher_key), secure=secure_cookies),
        frontend_origin=os.getenv("GLIDE_FRONTEND_ORIGIN", "http://localhost:5173"),
    )
    app.include_router(create_auth_router(auth_service))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:4173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-Glide-Session"],
    )

    app.include_router(health.router)
    app.include_router(demo.router)
    return app


app = create_app()
