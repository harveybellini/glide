from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError
from glide.api.auth import TokenBundle
from glide.deploy.credentials import (
    CredentialsUnavailableError,
    SecretsCredentialStore,
    StoredCredentials,
    secret_name_for,
)


class FakeSecretsClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requested_ids: list[str] = []
        self.deleted: list[dict] = []
        self.puts: list[tuple[str, str]] = []

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

    def put_secret_value(self, SecretId: str, SecretString: str) -> dict:
        self.puts.append((SecretId, SecretString))
        return {}

    def create_secret(self, Name: str, SecretString: str) -> dict:
        self.puts.append((Name, SecretString))
        return {}


def _bundle() -> TokenBundle:
    return TokenBundle(
        access_token="access",
        refresh_token="refresh",
        id_token="",
        email="",
        subject="",
        expires_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        scopes=("openid", "email"),
    )


class ScriptedSecretsClient:
    """Secrets client that raises scripted ``ClientError`` responses."""

    def __init__(self, put_errors=(), get_errors=()) -> None:
        self.put_errors = list(put_errors)
        self.get_errors = list(get_errors)
        self.puts: list[tuple[str, str]] = []
        self.creates: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def put_secret_value(self, SecretId: str, SecretString: str) -> dict:
        if self.put_errors:
            code = self.put_errors.pop(0)
            raise ClientError(
                {"Error": {"Code": code, "Message": "denied"}},
                "PutSecretValue",
            )
        self.puts.append((SecretId, SecretString))
        return {}

    def create_secret(self, Name: str, SecretString: str) -> dict:
        self.creates.append((Name, SecretString))
        return {}

    def get_secret_value(self, SecretId: str) -> dict:
        if self.get_errors:
            code = self.get_errors.pop(0)
            raise ClientError(
                {"Error": {"Code": code, "Message": "denied"}},
                "GetSecretValue",
            )
        return {"SecretString": json.dumps({"access_token": "access"})}

    def delete_secret(self, SecretId: str, ForceDeleteWithoutRecovery=True) -> dict:
        self.deleted.append(SecretId)
        return {}


def test_secret_names_avoid_invalid_characters_and_stay_injective() -> None:
    """Regression: ``glide/tokens/google:<sub>`` is an invalid secret name.

    The deployed callback exchanged the code successfully, then failed with
    ``ValidationException: Invalid name`` because Secrets Manager rejects the
    colon in the Google user id.
    """

    client = FakeSecretsClient({"access_token": "a", "refresh_token": "r"})
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    store.save("google:107455000000000000001", _bundle())

    name = client.puts[0][0]
    assert name.startswith("glide/tokens/")
    assert ":" not in name
    assert name == secret_name_for("glide/tokens", "google:107455000000000000001")
    assert name != secret_name_for("glide/tokens", "google-107455000000000000001")

    store.load("google:107455000000000000001")
    assert client.requested_ids[-1] == name


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

    assert client.requested_ids == [secret_name_for("glide/tokens", "google:subject")]
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
            "SecretId": secret_name_for("glide/tokens", "google:subject"),
            "ForceDeleteWithoutRecovery": True,
        }
    ]
    assert revoked == [
        {
            "url": "https://oauth2.googleapis.com/revoke",
            "params": {"token": "refresh"},
        }
    ]


def test_revoke_uses_provider_transport_by_default(monkeypatch) -> None:
    client = FakeSecretsClient(
        {"access_token": "access", "refresh_token": "refresh", "scopes": []}
    )
    revoked: list[tuple[str, dict | None]] = []
    monkeypatch.setattr(
        "glide.deploy.credentials.revoke_google_token",
        lambda url, *, params=None: revoked.append((url, params)),
    )
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    store.revoke("google:subject")

    assert revoked == [
        ("https://oauth2.googleapis.com/revoke", {"token": "refresh"})
    ]
    assert len(client.deleted) == 1


def test_refreshed_credentials_are_persisted_back() -> None:
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
    assert isinstance(credentials, StoredCredentials)
    credentials._on_refresh(  # noqa: SLF001 - exercised, not an implementation detail test
        "new-access",
        "new-refresh",
        datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        ["openid", "email"],
    )

    assert len(client.puts) == 1
    secret_id, payload = client.puts[0]
    assert secret_id == secret_name_for("glide/tokens", "google:subject")
    assert json.loads(payload)["access_token"] == "new-access"
    assert json.loads(payload)["refresh_token"] == "new-refresh"


def test_save_creates_secret_after_resource_not_found() -> None:
    client = ScriptedSecretsClient(put_errors=["ResourceNotFoundException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    store.save("google:subject", _bundle())

    assert client.puts == []
    assert len(client.creates) == 1
    name, payload = client.creates[0]
    assert name == secret_name_for("glide/tokens", "google:subject")
    assert json.loads(payload)["access_token"] == "access"


def test_save_reraises_unexpected_client_errors() -> None:
    client = ScriptedSecretsClient(put_errors=["AccessDeniedException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    with pytest.raises(ClientError):
        store.save("google:subject", _bundle())

    assert client.creates == []


def test_revoke_treats_missing_secret_as_already_revoked() -> None:
    client = ScriptedSecretsClient(get_errors=["ResourceNotFoundException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
        transport=lambda url, *, params=None: None,
    )

    store.revoke("google:subject")

    assert client.deleted == []


def test_revoke_reraises_unexpected_client_errors() -> None:
    client = ScriptedSecretsClient(get_errors=["AccessDeniedException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    with pytest.raises(ClientError):
        store.revoke("google:subject")

    assert client.deleted == []


def test_stored_credentials_refresh_persists_tokens(monkeypatch) -> None:
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

    def fake_refresh(self, request) -> None:
        self.token = "fresh-access"
        self.expiry = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(
        "google.oauth2.credentials.Credentials.refresh",
        fake_refresh,
    )

    credentials.refresh(object())

    assert credentials.token == "fresh-access"
    secret_id, payload = client.puts[0]
    assert secret_id == secret_name_for("glide/tokens", "google:subject")
    assert json.loads(payload)["access_token"] == "fresh-access"
    assert json.loads(payload)["refresh_token"] == "refresh"


def test_missing_secret_is_reported_as_a_missing_grant() -> None:
    """Regression: a deleted token secret must not surface as a 500.

    After the owner disconnected, ``GET /api/day`` still held a valid session
    cookie but the tenant's ``glide/tokens/*`` secret was gone, so Secrets
    Manager raised ``ResourceNotFoundException`` out of the calendar factory.
    The store now reports an empty bundle and lets the API answer with a
    reconnect prompt.
    """

    client = ScriptedSecretsClient(get_errors=["ResourceNotFoundException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    with pytest.raises(CredentialsUnavailableError):
        store.load("google:subject")

    # The same tenant counts as disconnected for the scope check rather than
    # raising out of the status endpoint.
    assert store.granted_scopes("google:subject") == ()


def test_load_still_reraises_other_secrets_errors() -> None:
    client = ScriptedSecretsClient(get_errors=["AccessDeniedException"])
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
    )

    with pytest.raises(ClientError):
        store.load("google:subject")


def test_revoke_skips_an_empty_secret_without_calling_google() -> None:
    client = FakeSecretsClient({})
    revoked: list[dict] = []
    store = SecretsCredentialStore(
        client=client,
        client_id="client-id",
        client_secret="client-secret",
        transport=lambda url, *, params=None: revoked.append(params or {}),
    )

    store.revoke("google:subject")

    assert revoked == []
    assert client.deleted == []
