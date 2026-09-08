from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from glide.api.auth import (
    DEFAULT_SCOPES,
    AuthService,
    ExpiringStateStore,
    FakeOAuthProvider,
    SessionCipher,
    SessionCookie,
    create_auth_router,
    ensure_required_scopes,
)


def _client(failures: int = 0) -> tuple[TestClient, FakeOAuthProvider]:
    provider = FakeOAuthProvider(failures=failures)
    service = AuthService(
        provider=provider,
        states=ExpiringStateStore(),
        cookies=SessionCookie(SessionCipher()),
        frontend_origin="http://localhost:5173/",
    )
    app = FastAPI()
    app.include_router(create_auth_router(service))
    return TestClient(app), provider


def test_oauth_start_callback_session_and_single_use_state() -> None:
    client, provider = _client()

    started = client.get("/api/auth/google/start", follow_redirects=False)
    assert started.status_code == 307
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    callback = client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 307
    assert "glide_session" in callback.cookies

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["email"] == "owner@example.com"
    assert provider._calls == ["authorize", "exchange"]

    replay = client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
    )
    assert replay.status_code == 403


def test_oauth_rejects_missing_or_failed_state() -> None:
    client, _ = _client()
    assert client.get("/api/auth/google/callback", params={"code": "x"}).status_code == 400
    assert client.get("/api/auth/google/callback", params={"state": "x"}).status_code == 400


def test_provider_failure_is_not_authenticated() -> None:
    client, _ = _client(failures=1)
    started = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    failed = client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
    )
    assert failed.status_code == 502
    assert "glide_session" not in failed.cookies


def test_logout_clears_session() -> None:
    client, _ = _client()
    started = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )

    logged_out = client.post("/api/auth/logout")
    assert logged_out.status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_status_reports_connection_and_provider_availability() -> None:
    client, _ = _client()

    initial = client.get("/api/auth/status")
    assert initial.status_code == 200
    assert initial.json() == {
        "connected": False,
        "email": None,
        "provider_available": True,
    }

    started = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )

    connected = client.get("/api/auth/status")
    assert connected.status_code == 200
    assert connected.json()["connected"] is True
    assert connected.json()["email"] == "owner@example.com"


def test_status_reports_unavailable_provider() -> None:
    from glide.api.auth import AuthStatus, UnavailableOAuthProvider

    service = AuthService(
        provider=UnavailableOAuthProvider(),
        states=ExpiringStateStore(),
        cookies=SessionCookie(SessionCipher()),
        frontend_origin="http://localhost:5173/",
    )
    app = FastAPI()
    app.include_router(create_auth_router(service))
    client = TestClient(app)

    payload = client.get("/api/auth/status").json()
    assert AuthStatus.model_validate(payload).provider_available is False
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 503


def test_required_scopes_are_enforced() -> None:
    ensure_required_scopes(list(DEFAULT_SCOPES), DEFAULT_SCOPES)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        ensure_required_scopes(["openid", "email"], DEFAULT_SCOPES)
    assert excinfo.value.status_code == 400
    assert "calendar.events.readonly" in excinfo.value.detail
