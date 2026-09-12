"""Application factory configuration and local-worker wiring.

Covers the environment-driven branches of ``glide.api.app`` that the HTTP
workflow tests never exercise: invalid poll intervals, a fully configured
OAuth provider, the unavailable calendar factory, and the local live
processor built from real (faked) boto3 clients.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from fastapi.testclient import TestClient
from glide.adapters.fixtures import canonical_settings
from glide.adapters.sqlite import SqliteStateStore
from glide.api.app import (
    _build_local_live_processor,
    _calendar_factory,
    _schedule_interval,
    _worker_poll_interval,
    create_app,
)
from glide.api.auth import (
    AuthSession,
    GoogleOAuthConfig,
    GoogleOAuthProvider,
    SessionCipher,
    TokenBundle,
)
from glide.deploy.credentials import CredentialsUnavailableError, InMemoryCredentialStore
from glide.domain.models import UserSettings
from glide.jobs.queue import Job
from glide.live.processor import LiveRunProcessor
from starlette.middleware.cors import CORSMiddleware

from tests.unit.test_amazon_location import FakePlacesClient, FakeRoutesClient


def _oauth_env(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/oauth/callback")


def test_worker_poll_interval_falls_back_on_bad_value(monkeypatch) -> None:
    monkeypatch.setenv("GLIDE_WORKER_POLL_INTERVAL", "garbage")
    assert _worker_poll_interval() == 1.0


def test_schedule_interval_falls_back_on_bad_value(monkeypatch) -> None:
    monkeypatch.setenv("GLIDE_SCHEDULE_INTERVAL", "garbage")
    assert _schedule_interval() == 300.0


def test_configured_oauth_selects_real_provider(monkeypatch, tmp_path) -> None:
    _oauth_env(monkeypatch)
    monkeypatch.setenv("GLIDE_SECURE_COOKIES", "1")
    store = SqliteStateStore(str(tmp_path / "glide.db"))

    app = create_app(state_store=store, run_local_worker=False)

    try:
        assert isinstance(app.state.auth_service.provider, GoogleOAuthProvider)
        assert isinstance(app.state.credential_store, InMemoryCredentialStore)
        assert app.state.credential_store.client_id == "client-id"
    finally:
        store.close()


def test_explicit_oauth_config_enables_provider_without_env_secret(
    monkeypatch, tmp_path
) -> None:
    """The deployed entrypoint passes a resolved config, not an env secret.

    Regression: ``glide.deploy.api`` resolves the Google client secret from
    Secrets Manager and must not need ``GOOGLE_CLIENT_SECRET`` in the Lambda
    environment, or the API silently serves ``provider_available: false``.
    """

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    store = SqliteStateStore(str(tmp_path / "glide.db"))
    config = GoogleOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://glide.example/api/auth/google/callback",
        secure_cookies=True,
    )

    app = create_app(
        state_store=store,
        run_local_worker=False,
        oauth_config=config,
    )

    try:
        assert isinstance(app.state.auth_service.provider, GoogleOAuthProvider)
        assert app.state.auth_service.cookies._secure is True
        with TestClient(app) as api:
            status = api.get("/api/auth/status").json()
            assert status["provider_available"] is True
            start = api.get("/api/auth/google/start", follow_redirects=False)
            assert start.status_code == 307
            assert start.headers["location"].startswith(
                "https://accounts.google.com/o/oauth2/auth"
            )
    finally:
        store.close()


def test_calendar_factory_without_credentials_raises() -> None:
    factory = _calendar_factory(None)

    with pytest.raises(RuntimeError, match="credentials are not configured"):
        factory(object())


def _cors_origins(app) -> list[str]:
    entries = [
        middleware.kwargs["allow_origins"]
        for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    ]
    assert len(entries) == 1
    return entries[0]


def test_production_cors_allows_only_the_configured_origin(
    monkeypatch, tmp_path
) -> None:
    """S5: dev origins must not widen the production trust boundary."""

    monkeypatch.setenv("GLIDE_ENV", "production")
    monkeypatch.setenv("GLIDE_FRONTEND_ORIGIN", "https://glide.example")
    store = SqliteStateStore(str(tmp_path / "glide.db"))

    app = create_app(state_store=store, run_local_worker=False)

    try:
        assert _cors_origins(app) == ["https://glide.example"]
    finally:
        store.close()


def test_development_cors_keeps_local_origins(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GLIDE_ENV", raising=False)
    monkeypatch.setenv("GLIDE_FRONTEND_ORIGIN", "http://localhost:8000")
    store = SqliteStateStore(str(tmp_path / "glide.db"))

    app = create_app(state_store=store, run_local_worker=False)

    try:
        assert _cors_origins(app) == [
            "http://localhost:8000",
            "http://localhost:5173",
            "http://localhost:4173",
        ]
    finally:
        store.close()


def test_calendar_factory_builds_adapter_from_stored_credentials(monkeypatch) -> None:
    store = InMemoryCredentialStore("client-id", "client-secret")
    store.save(
        "google:subject",
        TokenBundle(
            access_token="access",
            refresh_token="refresh",
            id_token="",
            email="",
            subject="",
            expires_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
            scopes=("openid",),
        ),
    )
    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, credentials):
            captured["credentials"] = credentials

    monkeypatch.setattr(
        "glide.adapters.google_calendar.GoogleCalendarAdapter",
        FakeAdapter,
    )

    settings = UserSettings.model_validate(
        canonical_settings(user_id="google:subject")
    )
    adapter = _calendar_factory(store)(settings)

    assert isinstance(adapter, FakeAdapter)
    assert captured["credentials"].token == "access"


def test_build_local_live_processor_wires_provider_clients(monkeypatch, tmp_path) -> None:
    clients = {"geo-places": FakePlacesClient(), "geo-routes": FakeRoutesClient()}
    calls = []

    def fake_client(service, region_name=None):
        calls.append((service, region_name))
        return clients[service]

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    store = SqliteStateStore(str(tmp_path / "glide.db"))

    processor = _build_local_live_processor(
        InMemoryCredentialStore("client-id", "client-secret"),
        store,
    )

    try:
        assert isinstance(processor, LiveRunProcessor)
        assert processor.place_search._client is clients["geo-places"]  # noqa: SLF001
        assert calls == [
            ("geo-places", "eu-west-2"),
            ("geo-routes", "eu-west-2"),
        ]
    finally:
        store.close()


def test_local_worker_routes_live_jobs_to_live_processor(monkeypatch, tmp_path) -> None:
    _oauth_env(monkeypatch)
    clients = {"geo-places": FakePlacesClient(), "geo-routes": FakeRoutesClient()}

    def fake_client(service, region_name=None):
        return clients[service]

    monkeypatch.setattr(boto3, "client", fake_client)
    store = SqliteStateStore(str(tmp_path / "glide.db"))
    app = create_app(state_store=store, schedule_interval=0)

    try:
        job = Job(
            id="job-1",
            user_id="google:subject",
            trigger="manual",
            run_id="run-1",
        )
        with pytest.raises(RuntimeError, match="no persisted settings"):
            app.state.worker.processor(job)
    finally:
        app.state.worker.stop()
        store.close()


def test_calendar_route_asks_for_reconnect_when_the_grant_is_gone(
    tmp_path,
) -> None:
    """Regression: a session cookie outliving its token secret answered 500.

    ``GET /api/day`` resolved the tenant's credentials while the browser still
    held a valid Glide session, so Secrets Manager's
    ``ResourceNotFoundException`` escaped as an internal error. The app now
    answers with a reconnect prompt the UI already knows how to show.
    """

    class MissingGrantStore:
        def load(self, user_id: str):
            raise CredentialsUnavailableError(user_id)

        def granted_scopes(self, user_id: str) -> tuple[str, ...]:
            return ()

    secret = "session-secret"
    store = SqliteStateStore(str(tmp_path / "glide.db"))
    store.save_settings(
        UserSettings.model_validate(canonical_settings(user_id="google:subject"))
    )
    app = create_app(
        state_store=store,
        run_local_worker=False,
        credential_store=MissingGrantStore(),
        session_secret=secret,
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    cookie = SessionCipher(key).encrypt(
        AuthSession(
            user_id="google:subject",
            email="owner@example.test",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    try:
        client = TestClient(
            app,
            raise_server_exceptions=False,
            cookies={"glide_session": cookie},
        )
        response = client.get("/api/day")
    finally:
        store.close()

    assert response.status_code == 409
    assert "Reconnect Google Calendar" in response.json()["detail"]
