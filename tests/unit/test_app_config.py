"""Application factory configuration and local-worker wiring.

Covers the environment-driven branches of ``glide.api.app`` that the HTTP
workflow tests never exercise: invalid poll intervals, a fully configured
OAuth provider, the unavailable calendar factory, and the local live
processor built from real (faked) boto3 clients.
"""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest
from glide.adapters.fixtures import canonical_settings
from glide.adapters.sqlite import SqliteStateStore
from glide.api.app import (
    _build_local_live_processor,
    _calendar_factory,
    _schedule_interval,
    _worker_poll_interval,
    create_app,
)
from glide.api.auth import GoogleOAuthProvider, TokenBundle
from glide.deploy.credentials import InMemoryCredentialStore
from glide.domain.models import UserSettings
from glide.jobs.queue import Job
from glide.live.processor import LiveRunProcessor

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


def test_calendar_factory_without_credentials_raises() -> None:
    factory = _calendar_factory(None)

    with pytest.raises(RuntimeError, match="credentials are not configured"):
        factory(object())


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
