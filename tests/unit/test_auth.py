from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from glide.api.auth import (
    DEFAULT_SCOPES,
    AuthService,
    AuthSession,
    FakeOAuthProvider,
    GoogleOAuthConfig,
    GoogleOAuthProvider,
    OAuthTransaction,
    SessionCipher,
    SessionCookie,
    TokenBundle,
    UnavailableOAuthProvider,
    create_auth_router,
    ensure_required_scopes,
)
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError


def _client(failures: int = 0) -> tuple[TestClient, FakeOAuthProvider]:
    provider = FakeOAuthProvider(failures=failures)
    service = AuthService(
        provider=provider,
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
        follow_redirects=False,
    )
    assert replay.status_code == 303
    assert "auth_error=state_expired" in replay.headers["location"]


def test_oauth_rejects_missing_or_failed_state() -> None:
    client, _ = _client()
    missing_code = client.get(
        "/api/auth/google/callback",
        params={"code": "x"},
        follow_redirects=False,
    )
    assert missing_code.status_code == 303
    assert "auth_error=missing_params" in missing_code.headers["location"]

    missing_state = client.get(
        "/api/auth/google/callback",
        params={"state": "x"},
        follow_redirects=False,
    )
    assert missing_state.status_code == 303
    assert "auth_error=missing_params" in missing_state.headers["location"]


def test_provider_failure_is_not_authenticated() -> None:
    client, _ = _client(failures=1)
    started = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    failed = client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert failed.status_code == 303
    assert "auth_error=exchange_failed" in failed.headers["location"]
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
        "requires_reconnect": False,
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
    assert "calendar.events.owned" in excinfo.value.detail


def test_unavailable_provider_exchange_raises_503() -> None:
    with pytest.raises(HTTPException) as exc_info:
        UnavailableOAuthProvider().exchange("code", "verifier")

    assert exc_info.value.status_code == 503


def test_real_provider_builds_flow_and_authorization_url() -> None:
    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    flow = provider._flow("verifier")  # noqa: SLF001
    assert flow.client_config["client_id"] == "client-id"

    url = provider.authorization_url("state-123", "verifier")
    assert "state=state-123" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_real_provider_exchange_returns_token_bundle(monkeypatch) -> None:
    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    class FakeCredentials:
        token = "access"
        refresh_token = "refresh"
        id_token = "id-token"
        scopes = list(DEFAULT_SCOPES)
        expiry = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    def fake_fetch_token(self, code=None, **kwargs):
        pass

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: FakeCredentials()))
    monkeypatch.setattr(
        "glide.api.auth.verify_oauth2_token",
        lambda id_token, request, audience=None: {
            "email": "owner@example.com",
            "sub": "subject-1",
        },
    )

    bundle = provider.exchange("code", "verifier")

    assert bundle.access_token == "access"
    assert bundle.refresh_token == "refresh"
    assert bundle.subject == "subject-1"
    assert bundle.email == "owner@example.com"
    assert bundle.expires_at == datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def test_real_provider_exchange_rejects_missing_identity(monkeypatch) -> None:
    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    class MissingIdToken:
        token = "access"
        refresh_token = None
        id_token = None
        scopes = list(DEFAULT_SCOPES)
        expiry = None

    def fake_fetch_token(self, code=None, **kwargs):
        pass

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: MissingIdToken()))

    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("code", "verifier")
    assert exc_info.value.status_code == 502


def test_real_provider_exchange_maps_a_rejected_code_to_a_retryable_failure(
    monkeypatch,
) -> None:
    """A stale, reused or forged code must never become an unhandled 500."""

    from google_auth_oauthlib.flow import Flow
    from oauthlib.oauth2.rfc6749.errors import InvalidGrantError

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    def rejected(self, code=None, **kwargs):
        raise InvalidGrantError(description="invalid_grant")

    monkeypatch.setattr(Flow, "fetch_token", rejected)

    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("reused-code", "verifier")

    assert exc_info.value.status_code == 401


def test_real_provider_exchange_maps_an_unverifiable_identity_token(
    monkeypatch,
) -> None:
    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    class Credentials:
        token = "access"
        refresh_token = "refresh"
        id_token = "forged"
        scopes = list(DEFAULT_SCOPES)
        expiry = None

    def fake_fetch_token(self, code=None, **kwargs):
        pass

    def unverifiable(id_token, request, audience=None):
        raise ValueError("Token signature is invalid")

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: Credentials()))
    monkeypatch.setattr("glide.api.auth.verify_oauth2_token", unverifiable)

    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("code", "verifier")

    assert exc_info.value.status_code == 401


def test_real_provider_exchange_rejects_missing_email_or_subject(
    monkeypatch,
) -> None:
    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    class Credentials:
        token = "access"
        refresh_token = None
        id_token = "id-token"
        scopes = list(DEFAULT_SCOPES)
        expiry = None

    def fake_fetch_token(self, code=None, **kwargs):
        pass

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: Credentials()))
    monkeypatch.setattr(
        "glide.api.auth.verify_oauth2_token",
        lambda id_token, request, audience=None: {"email": "owner@example.com"},
    )

    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("code", "verifier")
    assert exc_info.value.status_code == 502
    assert "missing email or subject" in exc_info.value.detail


def test_real_provider_exchange_defaults_expiry_and_checks_scopes(
    monkeypatch,
) -> None:
    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )

    class NoExpiry:
        token = "access"
        refresh_token = None
        id_token = "id-token"
        scopes = list(DEFAULT_SCOPES)
        expiry = None

    before = datetime.now(UTC)

    def fake_fetch_token(self, code=None, **kwargs):
        pass

    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: NoExpiry()))
    monkeypatch.setattr(
        "glide.api.auth.verify_oauth2_token",
        lambda id_token, request, audience=None: {
            "email": "owner@example.com",
            "sub": "subject-1",
        },
    )

    bundle = provider.exchange("code", "verifier")
    assert before <= bundle.expires_at <= datetime.now(UTC) + timedelta(hours=1)

    class WrongScopes:
        token = "access"
        refresh_token = None
        id_token = "id-token"
        scopes = ["openid"]
        expiry = None

    def narrow_fetch_token(self, code=None, **kwargs):
        pass

    monkeypatch.setattr(Flow, "fetch_token", narrow_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: WrongScopes()))
    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("code", "verifier")
    assert exc_info.value.status_code == 400


def test_required_scopes_accept_google_expanded_email_scope() -> None:
    """Regression: Google answers ``email`` with its ``userinfo.email`` URL.

    The deployed callback failed with ``Warning: Scope has changed`` because
    oauthlib raises that warning as an exception; the granted set is also
    validated here, and the expanded spelling must satisfy the guard.
    """

    ensure_required_scopes(
        [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/calendar.events.owned",
        ],
        DEFAULT_SCOPES,
    )

    with pytest.raises(HTTPException) as exc_info:
        ensure_required_scopes(["openid", "email"], DEFAULT_SCOPES)
    assert "calendar.events.owned" in exc_info.value.detail


def test_requires_reconnect_accepts_expanded_scope_and_rejects_legacy() -> None:
    class Scopes:
        def __init__(self, scopes: tuple[str, ...]) -> None:
            self._scopes = scopes

        def granted_scopes(self, user_id: str) -> tuple[str, ...]:
            del user_id
            return self._scopes

        def save(self, user_id: str, bundle) -> None:  # pragma: no cover
            del user_id, bundle

    current = AuthService(
        provider=FakeOAuthProvider(),
        cookies=SessionCookie(SessionCipher()),
        frontend_origin="http://localhost:5173/",
        credential_store=Scopes(
            (
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/calendar.events.owned",
            )
        ),
    )
    legacy = AuthService(
        provider=FakeOAuthProvider(),
        cookies=SessionCookie(SessionCipher()),
        frontend_origin="http://localhost:5173/",
        credential_store=Scopes(
            (
                "openid",
                "email",
                "https://www.googleapis.com/auth/calendar.app.created",
            )
        ),
    )

    assert current.requires_reconnect("google:subject") is False
    assert legacy.requires_reconnect("google:subject") is True


def test_exchange_relaxes_oauthlib_scope_change_warning(monkeypatch) -> None:
    import os

    from google_auth_oauthlib.flow import Flow

    provider = GoogleOAuthProvider(
        GoogleOAuthConfig(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://localhost:5173/oauth/callback",
        )
    )
    seen: dict[str, str | None] = {}

    class Credentials:
        token = "access"
        refresh_token = "refresh"
        id_token = "id-token"
        scopes = [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/calendar.events.owned",
        ]
        expiry = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    def fake_fetch_token(self, code=None, **kwargs):
        seen["relax"] = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")

    monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)
    monkeypatch.setattr(Flow, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(Flow, "credentials", property(lambda self: Credentials()))
    monkeypatch.setattr(
        "glide.api.auth.verify_oauth2_token",
        lambda id_token, request, audience=None: {
            "email": "owner@example.com",
            "sub": "subject-1",
        },
    )

    bundle = provider.exchange("code", "verifier")

    assert seen["relax"] == "1"
    assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in os.environ
    assert bundle.email == "owner@example.com"


def test_session_cipher_rejects_garbage_and_expired_tokens() -> None:
    cipher = SessionCipher()
    now = datetime.now(UTC)

    assert cipher.decrypt("garbage") is None
    expired = AuthSession(
        user_id="google:subject",
        email="owner@example.com",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(hours=1),
    )
    assert cipher.decrypt(cipher.encrypt(expired)) is None

    fresh = AuthSession(
        user_id="google:subject",
        email="owner@example.com",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    restored = cipher.decrypt(cipher.encrypt(fresh))
    assert restored is not None
    assert restored.user_id == "google:subject"


def test_transaction_cipher_rejects_garbage_and_expired_tokens() -> None:
    cipher = SessionCipher()
    now = datetime.now(UTC)

    assert cipher.decrypt_transaction("garbage") is None
    expired = OAuthTransaction(
        state="state",
        code_verifier="verifier",
        expires_at=now - timedelta(minutes=1),
    )
    assert cipher.decrypt_transaction(cipher.encrypt_transaction(expired)) is None

    fresh = OAuthTransaction(
        state="state-2",
        code_verifier="verifier-2",
        expires_at=now + timedelta(minutes=10),
    )
    restored = cipher.decrypt_transaction(cipher.encrypt_transaction(fresh))
    assert restored is not None
    assert restored.state == "state-2"


def test_disconnect_calls_on_disconnect_for_connected_session() -> None:
    revoked: list[str] = []
    cipher = SessionCipher()
    service = AuthService(
        provider=FakeOAuthProvider(),
        cookies=SessionCookie(cipher),
        frontend_origin="http://localhost:5173/",
        on_disconnect=lambda user_id: revoked.append(user_id),
    )
    session = AuthSession(
        user_id="google:subject",
        email="owner@example.com",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    request = SimpleNamespace(cookies={"glide_session": cipher.encrypt(session)})

    service.disconnect(request)

    assert revoked == ["google:subject"]


def test_callback_reports_oauth_denial_error() -> None:
    client, _ = _client()

    response = client.get(
        "/api/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "auth_error=access_denied" in response.headers["location"]


def test_fake_provider_rejects_pkce_verifier_mismatch() -> None:
    provider = FakeOAuthProvider()
    provider.authorization_url("state", "correct-verifier")

    with pytest.raises(HTTPException) as exc_info:
        provider.exchange("code", "wrong-verifier")

    assert exc_info.value.status_code == 403


def test_callback_maps_a_rejected_code_to_a_clean_gateway_error() -> None:
    """Regression: a malformed or replayed code used to escape as a 500.

    Google answers a bogus code with ``invalid_grant``; oauthlib raises it out
    of the provider, and the redirect endpoint used to hand the visitor a
    stack trace. It now reports a gateway error the app can show.
    """

    class RejectingProvider:
        def authorization_url(self, state: str, code_verifier: str) -> str:
            del code_verifier
            return f"http://provider.test/authorize?state={state}"

        def exchange(self, code: str, code_verifier: str) -> TokenBundle:
            del code, code_verifier
            raise InvalidGrantError(description="Malformed auth code.")

    service = AuthService(
        provider=RejectingProvider(),
        cookies=SessionCookie(SessionCipher()),
        frontend_origin="http://localhost:5173/",
    )
    app = FastAPI()
    app.include_router(create_auth_router(service))
    client = TestClient(app)

    started = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    response = client.get(
        "/api/auth/google/callback",
        params={"code": "bogus", "state": state},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "auth_error=exchange_failed" in response.headers["location"]
    assert "glide_session" not in response.cookies
