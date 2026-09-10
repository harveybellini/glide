"""Per-user Google token loading from Secrets Manager.

Deployment skeleton: the exact secret layout and rotation story are decided
once live credentials exist. Tokens are never logged or placed in source.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials

from glide.api.auth import TokenBundle

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def revoke_google_token(url: str, *, params: dict[str, str] | None = None) -> None:
    """Revoke a Google grant with a bounded provider-side HTTP request."""

    request = Request(
        url,
        data=urlencode(params or {}).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed OAuth URL
            if response.status >= 400:
                raise RuntimeError(f"Google token revocation failed: HTTP {response.status}")
    except HTTPError as exc:
        # Google returns 400 when a token is already invalid; the desired
        # provider-side state has already been reached in that case.
        if exc.code != 400:
            raise


class SecretsClient(Protocol):
    def get_secret_value(self, SecretId: str) -> dict[str, Any]: ...

    def delete_secret(
        self,
        SecretId: str,
        ForceDeleteWithoutRecovery: bool = True,
    ) -> dict[str, Any]: ...

    def create_secret(self, Name: str, SecretString: str) -> dict[str, Any]: ...

    def put_secret_value(self, SecretId: str, SecretString: str) -> dict[str, Any]: ...


class SecretsCredentialStore:
    def __init__(
        self,
        *,
        client: SecretsClient,
        client_id: str,
        client_secret: str,
        prefix: str = "glide/tokens",
        revoke_url: str = "https://oauth2.googleapis.com/revoke",
        transport: Callable[..., Any] | None = None,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._prefix = prefix
        self._revoke_url = revoke_url
        self._transport = transport or revoke_google_token

    def save(self, user_id: str, bundle: TokenBundle) -> None:
        secret_id = f"{self._prefix}/{user_id}"
        secret = json.dumps(
            {
                "access_token": bundle.access_token,
                "refresh_token": bundle.refresh_token,
                "expires_at": bundle.expires_at.isoformat(),
                "scopes": list(bundle.scopes),
            },
            separators=(",", ":"),
        )
        try:
            self._client.put_secret_value(SecretId=secret_id, SecretString=secret)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ResourceNotFoundException":
                raise
            self._client.create_secret(Name=secret_id, SecretString=secret)

    def load(self, user_id: str) -> Credentials:
        raw = self._raw(user_id)
        payload = json.loads(raw)
        return StoredCredentials(
            token=payload.get("access_token"),
            refresh_token=payload.get("refresh_token"),
            token_uri=GOOGLE_TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=payload.get("scopes", []),
            on_refresh=lambda access, refresh, expiry, scopes: self.save(
                user_id,
                TokenBundle(
                    access_token=access,
                    refresh_token=refresh,
                    id_token="",
                    email="",
                    subject="",
                    expires_at=expiry or datetime.now(UTC) + timedelta(hours=1),
                    scopes=tuple(scopes or ()),
                ),
            ),
        )

    def granted_scopes(self, user_id: str) -> tuple[str, ...]:
        payload = json.loads(self._raw(user_id))
        return tuple(payload.get("scopes", ()))

    def revoke(self, user_id: str) -> None:
        """Revoke the refresh grant and delete the stored secret."""

        secret_id = f"{self._prefix}/{user_id}"
        try:
            raw = self._raw(user_id)
            payload = json.loads(raw)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code != "ResourceNotFoundException":
                raise
            return  # the grant is already gone; nothing left to revoke
        self._transport(
            self._revoke_url,
            params={
                "token": payload.get("refresh_token")
                or payload.get("access_token", "")
            },
        )
        self._client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)

    def _raw(self, user_id: str) -> str:
        secret_id = f"{self._prefix}/{user_id}"
        return (
            self._client.get_secret_value(SecretId=secret_id).get("SecretString")
            or "{}"
        )


class StoredCredentials(Credentials):
    """``Credentials`` that persist refreshed tokens through a callback."""

    def __init__(self, *, on_refresh: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._on_refresh = on_refresh

    def refresh(self, request: Any) -> None:
        super().refresh(request)
        if self._on_refresh is not None:
            self._on_refresh(self.token, self.refresh_token, self.expiry, self.scopes)


@dataclass
class InMemoryCredentialStore:
    """Process-local credential storage for live-provider development."""

    client_id: str
    client_secret: str
    _bundles: dict[str, TokenBundle] = field(default_factory=dict)

    def save(self, user_id: str, bundle: TokenBundle) -> None:
        self._bundles[user_id] = bundle

    def load(self, user_id: str) -> Credentials:
        bundle = self._bundles[user_id]
        return Credentials(
            token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=list(bundle.scopes),
        )

    def granted_scopes(self, user_id: str) -> tuple[str, ...]:
        return self._bundles[user_id].scopes

    def revoke(self, user_id: str) -> None:
        self._bundles.pop(user_id, None)
