"""Server-side Google OAuth plumbing with an offline-testable provider seam.

Live verification requires the owner's OAuth client. The state, cookie,
identity-token, and failure checks are implemented here so the local sample
path can prove tenant isolation before real Google credentials exist.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2.id_token import verify_oauth2_token
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, ConfigDict

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
DEFAULT_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.app.created",
)


def ensure_required_scopes(
    granted: set[str] | tuple[str, ...] | list[str] | None,
    required: tuple[str, ...],
) -> None:
    """Reject a token that omitted any required OAuth scope."""

    missing = [scope for scope in required if scope not in set(granted or ())]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Required Google scopes were not granted: {', '.join(missing)}",
        )


class TokenBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str | None = None
    id_token: str
    email: str
    subject: str
    expires_at: datetime
    scopes: tuple[str, ...] = ()


class AuthSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    created_at: datetime
    expires_at: datetime


class AuthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    email: str | None = None
    provider_available: bool


class OAuthProvider(Protocol):
    def authorization_url(self, state: str) -> str: ...

    def exchange(self, code: str) -> TokenBundle: ...


class UnavailableOAuthProvider:
    """Installed route boundary when Google credentials are not configured."""

    def authorization_url(self, state: str) -> str:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured on this server.",
        )

    def exchange(self, code: str) -> TokenBundle:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured on this server.",
        )


@dataclass
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    secure_cookies: bool = False

    @classmethod
    def from_env(cls) -> GoogleOAuthConfig:
        missing = [
            name
            for name in (
                "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REDIRECT_URI",
            )
            if not os.getenv(name)
        ]
        if missing:
            raise ValueError(f"missing Google OAuth environment: {', '.join(missing)}")
        return cls(
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            redirect_uri=os.environ["GOOGLE_REDIRECT_URI"],
            secure_cookies=os.getenv("GLIDE_SECURE_COOKIES", "").lower() in {"1", "true"},
        )


@dataclass
class GoogleOAuthProvider:
    config: GoogleOAuthConfig

    def _flow(self) -> Flow:
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "auth_uri": GOOGLE_AUTH_URI,
                    "token_uri": GOOGLE_TOKEN_URI,
                }
            },
            scopes=list(self.config.scopes),
        )

    def authorization_url(self, state: str) -> str:
        flow = self._flow()
        flow.redirect_uri = self.config.redirect_uri
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        return authorization_url

    def exchange(self, code: str) -> TokenBundle:
        flow = self._flow()
        flow.redirect_uri = self.config.redirect_uri
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if credentials.id_token is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google response did not include an identity token.",
            )
        ensure_required_scopes(credentials.scopes, self.config.scopes)
        request = google_requests.Request()
        id_info = verify_oauth2_token(
            credentials.id_token,
            request,
            audience=self.config.client_id,
        )
        email = id_info.get("email")
        subject = id_info.get("sub")
        if not email or not subject:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google identity token is missing email or subject.",
            )
        if credentials.expiry is None:
            expires_at = datetime.now(UTC) + timedelta(hours=1)
        else:
            expires_at = credentials.expiry
        return TokenBundle(
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            id_token=credentials.id_token,
            email=email,
            subject=subject,
            expires_at=expires_at,
            scopes=tuple(credentials.scopes),
        )


class SessionCipher:
    """Local Fernet encryption placeholder for the KMS path in production."""

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = base64.urlsafe_b64encode(hashlib.sha256(secrets.token_bytes(32)).digest())
        self._fernet = Fernet(key)

    def encrypt(self, session: AuthSession) -> str:
        return self._fernet.encrypt(
            session.model_dump_json().encode("utf-8")
        ).decode("ascii")

    def decrypt(self, token: str) -> AuthSession | None:
        try:
            payload = self._fernet.decrypt(token.encode("ascii"))
            session = AuthSession.model_validate_json(payload)
        except (InvalidToken, ValueError):
            return None
        if session.expires_at <= datetime.now(UTC):
            return None
        return session


class ExpiringStateStore:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self._states: dict[str, float] = {}
        self._ttl = ttl_seconds

    def new(self) -> str:
        state = secrets.token_urlsafe(32)
        self._states[state] = time.monotonic()
        return state

    def consume(self, state: str) -> bool:
        created = self._states.pop(state, None)
        if created is None:
            return False
        return time.monotonic() - created <= self._ttl


class SessionCookie:
    def __init__(self, cipher: SessionCipher, secure: bool = False) -> None:
        self._cipher = cipher
        self._secure = secure

    def set(self, response: Response, session: AuthSession) -> None:
        response.set_cookie(
            key="glide_session",
            value=self._cipher.encrypt(session),
            max_age=3600,
            httponly=True,
            samesite="lax",
            secure=self._secure,
            path="/",
        )

    def clear(self, response: Response) -> None:
        response.delete_cookie("glide_session", path="/")

    def read(self, request: Request) -> AuthSession | None:
        token = request.cookies.get("glide_session")
        if not token:
            return None
        return self._cipher.decrypt(token)


@dataclass
class AuthService:
    provider: OAuthProvider
    states: ExpiringStateStore
    cookies: SessionCookie
    frontend_origin: str

    def start(self) -> str:
        state = self.states.new()
        return self.provider.authorization_url(state)

    def complete(self, code: str, state: str) -> AuthSession:
        if not self.states.consume(state):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OAuth state is missing or expired.",
            )
        bundle = self.provider.exchange(code)
        return AuthSession(
            user_id=f"google:{bundle.subject}",
            email=bundle.email,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def create_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status", response_model=AuthStatus)
    def auth_status(request: Request) -> AuthStatus:
        current = service.cookies.read(request)
        return AuthStatus(
            connected=current is not None,
            email=current.email if current is not None else None,
            provider_available=not isinstance(service.provider, UnavailableOAuthProvider),
        )

    @router.get("/google/start")
    def start() -> Response:
        return RedirectResponse(url=service.start(), status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @router.get("/google/callback")
    def callback(
        code: str = "",
        state: str = "",
        error: str | None = None,
    ) -> Response:
        if error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Google authorization failed: {error}",
            )
        if not code or not state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing OAuth code or state.",
            )
        session = service.complete(code, state)
        redirect = RedirectResponse(
            url=service.frontend_origin,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        service.cookies.set(redirect, session)
        return redirect

    @router.get("/session")
    def session(request: Request) -> AuthSession:
        current = service.cookies.read(request)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authenticated session.",
            )
        return current

    @router.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        service.cookies.clear(response)
        return {"status": "signed_out"}

    return router


@dataclass
class FakeOAuthProvider:
    """Deterministic offline provider for auth tests and local development."""

    email: str = "owner@example.com"
    subject: str = "fake-subject"
    failures: int = 0
    _calls: list[str] = field(default_factory=list)

    def authorization_url(self, state: str) -> str:
        self._calls.append("authorize")
        return f"http://fake-provider.test/authorize?state={state}"

    def exchange(self, code: str) -> TokenBundle:
        if self.failures > 0:
            self.failures -= 1
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="simulated provider failure",
            )
        self._calls.append("exchange")
        return TokenBundle(
            access_token="fake-access",
            refresh_token="fake-refresh",
            id_token="fake-id",
            email=self.email,
            subject=self.subject,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=DEFAULT_SCOPES,
        )
