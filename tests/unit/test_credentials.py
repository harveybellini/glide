from __future__ import annotations

import json

from glide.deploy.credentials import SecretsCredentialStore


class FakeSecretsClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requested_ids: list[str] = []
        self.deleted: list[dict] = []

    def get_secret_value(self, SecretId: str) -> dict:
        self.requested_ids.append(SecretId)
        return {"SecretString": json.dumps(self.payload)}

    def delete_secret(self, SecretId: str, ForceDeleteWithoutRecovery=True) -> dict:
        self.deleted.append(
            {
                "SecretId": SecretId,
                "ForceDeleteWithoutRecovery": ForceDeleteWithoutRecovery,
            }
        )
        return {}


def test_load_returns_refreshable_credentials_without_logging_tokens() -> None:
    client = FakeSecretsClient(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "scopes": ["openid", "email"],
        }
    )
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    credentials = store.load("google:subject")

    assert client.requested_ids == ["glide/tokens/google:subject"]
    assert credentials.token == "access"
    assert credentials.refresh_token == "refresh"
    assert credentials.client_id == "client-id"
    assert credentials.token_uri == "https://oauth2.googleapis.com/token"
    assert credentials.scopes == ["openid", "email"]


def test_revoke_deletes_the_secret_and_revokes_the_grant() -> None:
    client = FakeSecretsClient(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "scopes": ["openid", "email"],
        }
    )
    revoked: list[dict] = []

    def transport(url: str, *, params=None) -> None:
        revoked.append({"url": url, "params": params})

    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
        transport=transport,
    )

    store.revoke("google:subject")

    assert client.deleted == [
        {
            "SecretId": "glide/tokens/google:subject",
            "ForceDeleteWithoutRecovery": True,
        }
    ]
    assert revoked == [
        {
            "url": "https://oauth2.googleapis.com/revoke",
            "params": {"token": "access"},
        }
    ]
