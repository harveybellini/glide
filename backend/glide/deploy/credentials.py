"""Per-user Google token loading from Secrets Manager.

Deployment skeleton: the exact secret layout and rotation story are decided
once live credentials exist. Tokens are never logged or placed in source.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from google.oauth2.credentials import Credentials

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


class SecretsClient(Protocol):
    def get_secret_value(self, SecretId: str) -> dict[str, Any]: ...

    def delete_secret(
        self,
        SecretId: str,
        ForceDeleteWithoutRecovery: bool = True,
    ) -> dict[str, Any]: ...


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
        self._transport = transport

    def load(self, user_id: str) -> Credentials:
        raw = self._raw(user_id)
        payload = json.loads(raw)
        return Credentials(
            token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            token_uri=GOOGLE_TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=payload.get("scopes", []),
        )

    def revoke(self, user_id: str) -> None:
        """Revoke the refresh grant and delete the stored secret."""

        secret_id = f"{self._prefix}/{user_id}"
        raw = self._raw(user_id)
        payload = json.loads(raw)
        self._client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
        if self._transport is not None:
            self._transport(
                self._revoke_url,
                params={"token": payload.get("access_token", "")},
            )

    def _raw(self, user_id: str) -> str:
        secret_id = f"{self._prefix}/{user_id}"
        return (
            self._client.get_secret_value(SecretId=secret_id).get("SecretString")
            or "{}"
        )
