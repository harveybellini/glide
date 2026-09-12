"""Server-side Google OAuth plumbing with an offline-testable provider seam.

Live verification requires the owner's OAuth client. The state, cookie,
identity-token, and failure checks are implemented here so the local sample
path can prove tenant isolation before real Google credentials exist.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from pydantic import Field as PydanticField

from glide.adapters.interfaces import StateStore
from glide.domain.models import TravelMode, UserSettings

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

logger = logging.getLogger("glide.auth")

DEFAULT_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events.owned",
)

# Google answers the short ``email`` scope with its expanded URL form in the
# token response. Treat both spellings as the same grant so the scope guard
# keeps rejecting real omissions without rejecting Google's own expansion.
GOOGLE_SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}


def normalize_scope(scope: str) -> str:
    cleaned = scope.strip()
    return GOOGLE_SCOPE_ALIASES.get(cleaned, cleaned)


def missing_required_scopes(
    granted: set[str] | tuple[str, ...] | list[str] | None,
) -> list[str]:
    normalized = {normalize_scope(scope) for scope in granted or ()}
    return [scope for scope in DEFAULT_SCOPES if normalize_scope(scope) not in normalized]


def ensure_required_scopes(
    granted: set[str] | tuple[str, ...] | list[str] | None,
    required: tuple[str, ...],
) -> None:
    """Reject a token that omitted any required OAuth scope."""

    normalized = {normalize_scope(scope) for scope in granted or ()}
    missing = [scope for scope in required if normalize_scope(scope) not in normalized]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Required Google scopes were not granted: {', '.join(missing)}",
        )


@contextmanager
def _relaxed_token_scope() -> Iterator[None]:
    """Let oauthlib accept Google's expanded scope names on exchange.

    oauthlib raises the RFC 6749 scope-change ``Warning`` as an exception
    unless ``OAUTHLIB_RELAX_TOKEN_SCOPE`` is set. The granted scopes are still
    validated against the required set straight afterwards.
    """

    previous = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous


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
    requires_reconnect: bool = False


class DisconnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    warnings: list[str] = PydanticField(default_factory=list)


class OAuthTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    code_verifier: str
    expires_at: datetime


class OAuthProvider(Protocol):
    def authorization_url(self, state: str, code_verifier: str) -> str: ...

    def exchange(self, code: str, code_verifier: str) -> TokenBundle: ...


class CredentialWriter(Protocol):
    def save(self, user_id: str, bundle: TokenBundle) -> None: ...


class UnavailableOAuthProvider:
    """Installed route boundary when Google credentials are not configured."""

    def authorization_url(self, state: str, code_verifier: str) -> str:
        del state, code_verifier
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured on this server.",
        )

    def exchange(self, code: str, code_verifier: str) -> TokenBundle:
        del code, code_verifier
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

    def _flow(self, code_verifier: str) -> Flow:
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
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )

    def authorization_url(self, state: str, code_verifier: str) -> str:
        flow = self._flow(code_verifier)
        flow.redirect_uri = self.config.redirect_uri
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        return authorization_url

    def exchange(self, code: str, code_verifier: str) -> TokenBundle:
        flow = self._flow(code_verifier)
        flow.redirect_uri = self.config.redirect_uri
        with _relaxed_token_scope():
            try:
                flow.fetch_token(code=code)
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001 - library error surface
                # A stale, reused or forged code (oauthlib raises
                # InvalidGrantError), a transport failure, or an unexpected
                # library error used to escape the callback as an unhandled
                # Lambda 500. The user can always start the sign-in again, so
                # every one of them becomes a retryable 401.
                logger.warning(
                    "google token exchange failed: %s", type(exc).__name__
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=(
                        "Google sign-in could not be completed. "
                        "Please start again from the app."
                    ),
                ) from exc
        credentials = flow.credentials
        if credentials.id_token is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google response did not include an identity token.",
            )
        ensure_required_scopes(credentials.scopes, self.config.scopes)
        request = google_requests.Request()
        try:
            id_info = verify_oauth2_token(
                credentials.id_token,
                request,
                audience=self.config.client_id,
            )
        except ValueError as exc:
            # google-auth raises ValueError for a token it cannot verify.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google returned an identity token we could not verify. Please start again.",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - certificate/transport failures
            logger.warning(
                "google identity verification failed: %s", type(exc).__name__
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Google sign-in could not be verified right now. Please try again.",
            ) from exc
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
        return self._encrypt(session.model_dump_json())

    def decrypt(self, token: str) -> AuthSession | None:
        try:
            payload = self._fernet.decrypt(token.encode("ascii"))
            session = AuthSession.model_validate_json(payload)
        except (InvalidToken, ValueError):
            return None
        if session.expires_at <= datetime.now(UTC):
            return None
        return session

    def encrypt_transaction(self, transaction: OAuthTransaction) -> str:
        return self._encrypt(transaction.model_dump_json())

    def decrypt_transaction(self, token: str) -> OAuthTransaction | None:
        try:
            payload = self._fernet.decrypt(token.encode("ascii"))
            transaction = OAuthTransaction.model_validate_json(payload)
        except (InvalidToken, ValueError):
            return None
        if transaction.expires_at <= datetime.now(UTC):
            return None
        return transaction

    def _encrypt(self, payload: str) -> str:
        return self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")


class SessionCookie:
    def __init__(self, cipher: SessionCipher, secure: bool = False) -> None:
        self._cipher = cipher
        self._secure = secure

    def set(self, response: Response, session: AuthSession) -> None:
        response.set_cookie(
            key="glide_session",
            value=self._cipher.encrypt(session),
            max_age=max(
                0,
                int((session.expires_at - datetime.now(UTC)).total_seconds()),
            ),
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

    def set_transaction(self, response: Response, transaction: OAuthTransaction) -> None:
        response.set_cookie(
            key="glide_oauth_transaction",
            value=self._cipher.encrypt_transaction(transaction),
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=self._secure,
            path="/api/auth/google/callback",
        )

    def clear_transaction(self, response: Response) -> None:
        response.delete_cookie(
            "glide_oauth_transaction",
            path="/api/auth/google/callback",
        )

    def read_transaction(self, request: Request) -> OAuthTransaction | None:
        token = request.cookies.get("glide_oauth_transaction")
        if not token:
            return None
        return self._cipher.decrypt_transaction(token)


@dataclass
class AuthService:
    provider: OAuthProvider
    cookies: SessionCookie
    frontend_origin: str
    credential_store: CredentialWriter | None = None
    state_store: StateStore | None = None
    on_disconnect: Callable[[str], object] | None = None

    def requires_reconnect(self, user_id: str) -> bool:
        """Flag stored legacy grants that cannot write primary-calendar events."""

        scope_reader = getattr(self.credential_store, "granted_scopes", None)
        if scope_reader is None:
            return False
        try:
            granted = set(scope_reader(user_id))
        except Exception:  # noqa: BLE001 - status must remain available
            return True
        return bool(missing_required_scopes(granted))

    def start(self) -> tuple[str, OAuthTransaction]:
        transaction = OAuthTransaction(
            state=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        return (
            self.provider.authorization_url(
                transaction.state,
                transaction.code_verifier,
            ),
            transaction,
        )

    def complete(self, request: Request, code: str, state: str) -> AuthSession:
        transaction = self.cookies.read_transaction(request)
        if transaction is None or not secrets.compare_digest(transaction.state, state):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="OAuth state is missing or expired.",
            )
        bundle = self.provider.exchange(code, transaction.code_verifier)
        user_id = f"google:{bundle.subject}"
        if self.credential_store is not None:
            self.credential_store.save(user_id, bundle)
        if self.state_store is not None:
            existing = self.state_store.get_settings(user_id)
            if existing is None:
                self.state_store.save_settings(
                    UserSettings(
                        user_id=user_id,
                        time_zone="UTC",
                        source_calendar_id="primary",
                        glide_calendar_id="primary",
                        mode=TravelMode.DRIVING,
                        padding_minutes=10,
                        # The address the user just signed in with is the
                        # natural place to send "needs your decision" mail;
                        # they can change or pause it in Settings.
                        notification_email=bundle.email,
                        enabled=False,
                        revision=1,
                    )
                )
            else:
                updates: dict[str, object] = {}
                if existing.glide_calendar_id != "primary":
                    updates["glide_calendar_id"] = "primary"
                    updates["legacy_glide_calendar_id"] = (
                        existing.glide_calendar_id
                        or existing.legacy_glide_calendar_id
                    )
                if existing.notification_email is None:
                    updates["notification_email"] = bundle.email
                if updates:
                    updates["revision"] = existing.revision + 1
                    self.state_store.save_settings(
                        existing.model_copy(update=updates)
                    )
        now = datetime.now(UTC)
        return AuthSession(
            user_id=user_id,
            email=bundle.email,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )

    def disconnect(self, request: Request) -> DisconnectResponse:
        current = self.cookies.read(request)
        if current is not None and self.on_disconnect is not None:
            result = self.on_disconnect(current.user_id)
            if isinstance(result, BaseModel):
                return DisconnectResponse.model_validate(result.model_dump())
            if isinstance(result, dict):
                return DisconnectResponse.model_validate(result)
        return DisconnectResponse(status="signed_out")


def create_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status", response_model=AuthStatus)
    def auth_status(request: Request) -> AuthStatus:
        current = service.cookies.read(request)
        return AuthStatus(
            connected=current is not None,
            email=current.email if current is not None else None,
            provider_available=not isinstance(service.provider, UnavailableOAuthProvider),
            requires_reconnect=(
                service.requires_reconnect(current.user_id)
                if current is not None
                else False
            ),
        )

    @router.get("/google/start")
    def start() -> Response:
        url, transaction = service.start()
        redirect = RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        service.cookies.set_transaction(redirect, transaction)
        return redirect

    @router.get("/google/callback")
    def callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str | None = None,
    ) -> Response:
        def error_redirect(code_name: str) -> RedirectResponse:
            """Send the browser back to the app instead of a raw JSON page.

            Every failure the visitor can reach (cancelled consent, an expired
            transaction cookie, a replayed authorisation code) used to render as
            a JSON error document. The app now owns the messaging: it reads
            ``?auth_error=<code>``, shows a friendly notice, and the visitor can
            start the connection again from the landing page.
            """
            target = service.frontend_origin.rstrip("/")
            redirect = RedirectResponse(
                url=f"{target}/?auth_error={code_name}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
            service.cookies.clear_transaction(redirect)
            return redirect

        if error:
            # access_denied is the only provider code the UI treats specially;
            # everything else collapses to a generic retry message.
            return error_redirect(
                "access_denied" if error == "access_denied" else "provider_error"
            )
        if not code or not state:
            return error_redirect("missing_params")
        try:
            session = service.complete(request, code, state)
        except HTTPException as exc:
            # A missing, replayed or expired transaction cookie (403, and the
            # PKCE-mismatch guard) is a retry-from-the-app case; a provider or
            # transport failure (5xx) is reported as an exchange failure.
            return error_redirect(
                "state_expired"
                if exc.status_code < status.HTTP_500_INTERNAL_SERVER_ERROR
                else "exchange_failed"
            )
        except Exception as exc:  # noqa: BLE001 - provider exchange is third-party
            # A replayed, expired, truncated, or otherwise rejected code comes
            # back from Google as an oauthlib error; a network problem comes
            # back as a requests error. Both are reported here instead of
            # escaping as an opaque 500 from the redirect endpoint.
            logger.warning("Google OAuth exchange failed", exc_info=exc)
            return error_redirect("exchange_failed")
        redirect = RedirectResponse(
            url=service.frontend_origin,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        service.cookies.clear_transaction(redirect)
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
    def logout(request: Request, response: Response) -> DisconnectResponse:
        result = service.disconnect(request)
        service.cookies.clear(response)
        return result

    return router


@dataclass
class FakeOAuthProvider:
    """Deterministic offline provider for auth tests and local development."""

    email: str = "owner@example.com"
    subject: str = "fake-subject"
    failures: int = 0
    _calls: list[str] = field(default_factory=list)

    _verifier: str | None = None

    def authorization_url(self, state: str, code_verifier: str) -> str:
        self._calls.append("authorize")
        self._verifier = code_verifier
        return f"http://fake-provider.test/authorize?state={state}"

    def exchange(self, code: str, code_verifier: str) -> TokenBundle:
        if self.failures > 0:
            self.failures -= 1
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="simulated provider failure",
            )
        self._calls.append("exchange")
        if code_verifier != self._verifier:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PKCE verifier mismatch",
            )
        return TokenBundle(
            access_token="fake-access",
            refresh_token="fake-refresh",
            id_token="fake-id",
            email=self.email,
            subject=self.subject,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=DEFAULT_SCOPES,
        )
