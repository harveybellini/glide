from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from glide import __version__
from glide.adapters.interfaces import StateStore
from glide.adapters.sqlite import SqliteStateStore
from glide.agent.strands_runner import build_agent_runner
from glide.api.auth import (
    AuthService,
    GoogleOAuthConfig,
    GoogleOAuthProvider,
    SessionCipher,
    SessionCookie,
    UnavailableOAuthProvider,
    create_auth_router,
)
from glide.api.demo_store import DemoSessionStore
from glide.api.routes import demo, health, live
from glide.api.run_service import build_run_processor
from glide.deploy.credentials import CredentialsUnavailableError, InMemoryCredentialStore
from glide.jobs.dispatcher import LocalDispatcher
from glide.jobs.queue import InMemoryJobQueue, JobQueue
from glide.jobs.schedule_state import (
    InMemoryScheduleStateStore,
    ScheduleStateStore,
)
from glide.jobs.worker import LocalWorker
from glide.live.disconnect import DisconnectService


def _worker_poll_interval() -> float:
    raw = os.getenv("GLIDE_WORKER_POLL_INTERVAL", "1.0")
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value > 0 else 1.0


def _schedule_interval() -> float:
    # The deployed EventBridge rule ticks every five minutes. Locally the
    # dispatcher polls faster so a developer or judge watching the sample sees
    # the agent act without pressing anything; the per-tenant
    # ``background_interval_minutes`` still governs how often any tenant is
    # actually checked, so this changes latency, not the cost ceiling.
    raw = os.getenv("GLIDE_SCHEDULE_INTERVAL", "20.0")
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return value if value >= 0 else 20.0


def create_app(
    *,
    state_store: StateStore | None = None,
    job_queue: JobQueue | None = None,
    run_local_worker: bool = True,
    schedule_interval: float | None = None,
    credential_store: Any | None = None,
    place_search: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    session_secret: str | None = None,
    oauth_config: GoogleOAuthConfig | None = None,
    schedule_store: ScheduleStateStore | None = None,
) -> FastAPI:
    if state_store is None:
        state_store = SqliteStateStore(os.getenv("GLIDE_LOCAL_DB", "glide-local.db"))
    demo_store = DemoSessionStore(
        agent_runner=build_agent_runner(),
        state_store=state_store,
    )
    queue = job_queue if job_queue is not None else InMemoryJobQueue()
    worker: LocalWorker | None = None
    interval = (
        _schedule_interval() if schedule_interval is None else schedule_interval
    )
    dispatcher: LocalDispatcher | None = None
    schedule_store = schedule_store or InMemoryScheduleStateStore()
    if run_local_worker and interval > 0:
        dispatcher = LocalDispatcher(
            demo_store=demo_store,
            state_store=state_store,
            queue=queue,
            poll_interval_seconds=interval,
            schedule_store=schedule_store,
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

    @app.exception_handler(CredentialsUnavailableError)
    def _missing_grant(_: Any, __: CredentialsUnavailableError) -> JSONResponse:
        """Answer a revoked or cleaned-up grant with a reconnect prompt.

        A browser can still hold a valid Glide session cookie after the stored
        Google grant is gone (disconnect, revoked access, cleanup). That is a
        client-side state problem, not an outage, so it must not surface as a
        500 from the calendar routes.
        """

        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "Google Calendar is no longer connected for this account. "
                    "Reconnect Google Calendar to continue."
                )
            },
        )
    app.state.demo_store = demo_store
    app.state.state_store = state_store
    app.state.schedule_store = schedule_store
    app.state.queue = queue
    app.state.worker = worker
    app.state.dispatcher = dispatcher
    app.state.place_search = place_search
    app.state.clock = clock or (lambda: datetime.now(UTC))

    try:
        # Deployed entrypoints resolve the client secret from Secrets Manager
        # and pass a complete config; they deliberately never put the value in
        # an environment variable. Local development falls back to env vars.
        config = oauth_config or GoogleOAuthConfig.from_env()
        provider = GoogleOAuthProvider(config)
        secure_cookies = config.secure_cookies
        if credential_store is None:
            credential_store = InMemoryCredentialStore(
                client_id=config.client_id,
                client_secret=config.client_secret,
            )
    except ValueError:
        provider = UnavailableOAuthProvider()
        secure_cookies = False

    if session_secret is None:
        # Local development reads the key from the environment; the deployed
        # entrypoint resolves it from Secrets Manager and passes it in.
        session_secret = os.getenv("GLIDE_SESSION_SECRET")
    cipher_key = (
        base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())
        if session_secret
        else None
    )
    auth_service = AuthService(
        provider=provider,
        cookies=SessionCookie(SessionCipher(cipher_key), secure=secure_cookies),
        frontend_origin=os.getenv("GLIDE_FRONTEND_ORIGIN", "http://localhost:5173"),
        credential_store=credential_store,
        state_store=state_store,
    )
    app.state.calendar_factory = _calendar_factory(credential_store)
    if credential_store is not None:
        from glide.adapters.google_calendar import GoogleCalendarAdapter

        disconnect_service = DisconnectService(
            state_store=state_store,
            revoker=credential_store,
            calendar_factory=lambda settings: GoogleCalendarAdapter(
                credential_store.load(settings.user_id)
            ),
        )
        auth_service.on_disconnect = disconnect_service.disconnect
    if run_local_worker and isinstance(queue, InMemoryJobQueue):
        sample_processor = build_run_processor(demo_store, state_store)
        live_processor = (
            _build_local_live_processor(credential_store, state_store)
            if credential_store is not None
            else None
        )

        def route(job):  # noqa: ANN001
            if job.user_id.startswith("sample-"):
                return sample_processor(job)
            if live_processor is None:
                from glide.api.run_service import persist_failure

                persist_failure(state_store, job, "LiveProcessorUnavailable")
                raise RuntimeError(
                    f"no live processor available for user {job.user_id}"
                )
            return live_processor.process(job)

        worker = LocalWorker(
            queue=queue,
            processor=route,
            poll_interval_seconds=_worker_poll_interval(),
        )
    app.state.auth_service = auth_service
    app.state.credential_store = credential_store
    app.state.worker = worker
    app.include_router(create_auth_router(auth_service))

    frontend_origin = os.getenv("GLIDE_FRONTEND_ORIGIN", "http://localhost:5173")
    allowed_origins = [frontend_origin]
    if os.getenv("GLIDE_ENV", "").strip().lower() != "production":
        # Local Vite dev/preview servers only. Production trusts exactly the
        # configured frontend origin so a hostile local page cannot make
        # credentialed cross-origin calls.
        allowed_origins.extend(
            ["http://localhost:5173", "http://localhost:4173"]
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-Glide-Session"],
    )

    @app.middleware("http")
    async def harden_api_responses(request: Request, call_next):
        """Keep session-scoped JSON out of shared caches and content sniffing.

        Every route on this app answers private data (a user's calendar, runs
        and settings) for the credentials presented, so a browser or
        intermediary must never store the response, and the body must never be
        re-interpreted as another content type. Static assets are served by
        CloudFront, not here, so no route needs a cacheable answer.
        """

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.include_router(health.router)
    app.include_router(demo.router)
    app.include_router(live.router)
    return app


def _calendar_factory(credential_store: Any | None):
    """Default live calendar factory backed by stored Google credentials."""

    if credential_store is None:
        def unavailable(settings):  # noqa: ANN001
            del settings
            raise RuntimeError("Google credentials are not configured.")

        return unavailable

    from glide.adapters.google_calendar import GoogleCalendarAdapter

    def factory(settings):  # noqa: ANN001
        return GoogleCalendarAdapter(credential_store.load(settings.user_id))

    return factory


def _build_local_live_processor(credential_store: Any, state_store: StateStore):
    """Build the live processor for the in-process local worker.

    Provider clients are constructed lazily by boto3 and only fail when the
    first real call runs, so local sample development never needs AWS access.
    """

    import boto3

    from glide.adapters.amazon_location import (
        AmazonLocationPlaces,
        AmazonLocationRouter,
    )
    from glide.live.processor import build_live_processor

    region = os.getenv("AWS_REGION")
    places_client = boto3.client("geo-places", region_name=region)
    routes_client = boto3.client("geo-routes", region_name=region)
    places = AmazonLocationPlaces(places_client)
    router = AmazonLocationRouter(
        places_client=places_client,
        routes_client=routes_client,
    )
    return build_live_processor(
        state_store=state_store,
        calendar_factory=_calendar_factory(credential_store),
        router_factory=lambda settings: router,
        place_search=places,
        runner=build_agent_runner(),
        notifier=_build_local_notifier(),
    )


def _build_local_notifier():
    """Opt-in SES notifier for local live testing.

    Unset ``GLIDE_NOTIFICATION_FROM`` (the default) means local runs never
    contact SES, so sample development and tests stay offline.
    """

    from glide.adapters.notifications import SesDecisionNotifier

    from_address = os.getenv("GLIDE_NOTIFICATION_FROM")
    if not from_address:
        return None
    import boto3

    return SesDecisionNotifier(
        client=boto3.client("sesv2", region_name=os.getenv("AWS_REGION")),
        from_address=from_address,
        base_url=os.getenv("GLIDE_PUBLIC_BASE_URL"),
        configuration_set=os.getenv("GLIDE_NOTIFICATION_CONFIGURATION_SET") or None,
    )


app = None if os.getenv("GLIDE_ENV") == "production" else create_app()
