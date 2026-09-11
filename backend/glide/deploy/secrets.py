"""Runtime resolution of long-lived secrets from AWS Secrets Manager.

Deployed functions must not receive secret values through environment
variables: anything able to read a Lambda configuration would otherwise
obtain the session-encryption key or the Google OAuth client secret. The
functions keep only secret ARNs and resolve the values in memory on cold
start (each process caches what it resolves, and a new cold start re-reads
it so rotation is picked up).

Errors raised here name the secret id only; they never include the value.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class SecretsManager(Protocol):
    def get_secret_value(self, SecretId: str) -> dict[str, Any]: ...


def resolve_secret_string(client: SecretsManager, secret_id: str) -> str:
    """Return the plain-text secret for ``secret_id``."""

    response = client.get_secret_value(SecretId=secret_id)
    value = response.get("SecretString")
    if not isinstance(value, str):
        raise RuntimeError(f"secret {secret_id} has no SecretString")
    if not value.strip():
        raise RuntimeError(f"secret {secret_id} is empty")
    return value


def resolve_secret_key(client: SecretsManager, secret_id: str, key: str) -> str:
    """Return one string field of a JSON secret document."""

    payload = resolve_secret_string(client, secret_id)
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"secret {secret_id} is not a JSON document") from exc
    value = document.get(key) if isinstance(document, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"secret {secret_id} has no {key} string")
    return value
