from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from glide.adapters.fixtures import FixtureCalendar
from glide.adapters.sqlite import SqliteStateStore
from glide.api.app import create_app
from glide.api.auth import FakeOAuthProvider
from glide.deploy.credentials import InMemoryCredentialStore
from glide.domain.models import (
    Decision,
    DecisionStatus,
    PlaceRef,
    RunStatus,
    StoragePolicyStatus,
)

from tests.unit.test_live_processor import FakeLiveCalendar

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()


class FakePlaceLookup:
    def __init__(self, results: list[PlaceRef] | None = None) -> None:
        self.results = results or []
        self.calls: list[dict] = []

    def search(self, *, query, region=None, storage_allowed=False):
        self.calls.append(
            {
                "query": query,
                "region": region,
                "storage_allowed": storage_allowed,
            }
        )
        return self.results


def _connect(client: TestClient, provider: FakeOAuthProvider) -> str:
    started = client.get("/api/auth/google/start", follow_redirects=False)
    assert started.status_code == 307
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    callback = client.get(
        "/api/auth/google/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 307
    return state


def _live_app(tmp_path):
    store = SqliteStateStore(str(tmp_path / "glide.db"))
    app = create_app(
        state_store=store,
        run_local_worker=False,
        schedule_interval=0,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )
    return app, store


def test_live_user_connects_and_drives_shared_routes(tmp_path) -> None:
    app, store = _live_app(tmp_path)
    provider = FakeOAuthProvider(subject="google-subject-a")
    app.state.auth_service.provider = provider
    credential_store = InMemoryCredentialStore(
        client_id="client-id", client_secret="client-secret"
    )
    app.state.auth_service.credential_store = credential_store
    calendar = FakeLiveCalendar(FixtureCalendar(day=DAY).events())
    app.state.calendar_factory = lambda settings: calendar
    client = TestClient(app)

    assert client.get("/api/me").status_code == 401

    _connect(client, provider)

    assert "google:google-subject-a" in credential_store._bundles
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["user_id"] == "google:google-subject-a"
    assert me.json()["enabled"] is False

    day = client.get("/api/day")
    assert day.status_code == 200
    assert day.json()["label"] == "Your calendar - real routes"
    assert len(day.json()["source_events"]) == 3

    patched = client.patch("/api/settings", json={"padding_minutes": 15})
    assert patched.status_code == 200
    assert patched.json()["revision"] == 2
    assert patched.json()["padding_minutes"] == 15

    queued = client.post("/api/runs", json={"trigger": "live"})
    assert queued.status_code == 202
    run = store.get_run(queued.json()["run_id"])
    assert run is not None
    assert run.trigger == "live"
    assert run.status == RunStatus.QUEUED
    queued_jobs = list(app.state.queue._jobs.values())  # noqa: SLF001
    assert len(queued_jobs) == 1
    assert queued_jobs[0].user_id == "google:google-subject-a"

    resumed = client.post("/api/resume")
    assert resumed.status_code == 200
    assert resumed.json()["enabled"] is True
    assert resumed.json()["revision"] == 3


def test_second_live_user_cannot_read_or_change_first_user_data(tmp_path) -> None:
    app, store = _live_app(tmp_path)
    provider_a = FakeOAuthProvider(subject="google-subject-a")
    app.state.auth_service.provider = provider_a
    app.state.auth_service.credential_store = InMemoryCredentialStore(
        client_id="client-id", client_secret="client-secret"
    )
    app.state.calendar_factory = lambda settings: FakeLiveCalendar(
        FixtureCalendar(day=DAY).events()
    )
    client_a = TestClient(app)
    _connect(client_a, provider_a)
    queued = client_a.post("/api/runs", json={"trigger": "live"})
    run_id = queued.json()["run_id"]

    provider_b = FakeOAuthProvider(subject="google-subject-b")
    app.state.auth_service.provider = provider_b
    client_b = TestClient(app)
    _connect(client_b, provider_b)

    assert client_b.get(f"/api/runs/{run_id}").status_code == 404
    assert client_b.get("/api/activity").json()["receipts"] == []
    assert client_b.get("/api/decisions").json()["decisions"] == []

    changed = client_b.patch("/api/settings", json={"padding_minutes": 42})
    assert changed.status_code == 200
    assert changed.json()["user_id"] == "google:google-subject-b"
    assert client_a.get("/api/me").json()["padding_minutes"] == 10


def test_live_decision_resolution_persists_choice_and_requeues(tmp_path) -> None:
    app, store = _live_app(tmp_path)
    provider = FakeOAuthProvider(subject="google-subject-a")
    app.state.auth_service.provider = provider
    app.state.auth_service.credential_store = InMemoryCredentialStore(
        client_id="client-id", client_secret="client-secret"
    )
    app.state.calendar_factory = lambda settings: FakeLiveCalendar(
        FixtureCalendar(day=DAY).events()
    )
    client = TestClient(app)
    _connect(client, provider)

    user_id = "google:google-subject-a"
    decision = Decision(
        id=f"decision-{user_id}-occ-b-rev",
        user_id=user_id,
        occurrence_id="occ_b",
        journey_key="journey-b",
        source_revision="rev",
        reason="insufficient_time",
        calculated_facts={},
        allowed_actions=("correct_location", "skip_journey", "edit_source_event"),
    )
    store.save_decisions([decision])

    rejected = client.post(
        f"/api/decisions/{decision.id}/resolve",
        json={"action": "not_allowed"},
    )
    assert rejected.status_code == 400

    resolved = client.post(
        f"/api/decisions/{decision.id}/resolve",
        json={"action": "skip_journey"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["decision"]["status"] == DecisionStatus.RESOLVED.value
    assert resolved.json()["decision"]["resolution"] == "skip_journey"

    persisted = next(
        candidate
        for candidate in store.get_decisions(user_id)
        if candidate.id == decision.id
    )
    assert persisted.resolution == "skip_journey"
    queued_jobs = [
        job
        for job in app.state.queue._jobs.values()  # noqa: SLF001
        if job.trigger == "decision"
    ]
    assert len(queued_jobs) == 1
    assert store.get_run(queued_jobs[0].run_id) is not None


def test_sample_session_flow_is_unchanged_by_live_wiring(tmp_path) -> None:
    app, _ = _live_app(tmp_path)
    client = TestClient(app)

    created = client.post("/api/demo/session", json={})
    assert created.status_code == 201
    session_id = created.json()["session"]["session_id"]
    headers = {"X-Glide-Session": session_id}

    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user_id"].startswith("sample-")
    day = client.get("/api/day", headers=headers)
    assert day.status_code == 200
    assert day.json()["label"] == "Sample calendar - simulated routes"


def _connected_live_client(tmp_path) -> tuple[TestClient, object]:
    app, _ = _live_app(tmp_path)
    provider = FakeOAuthProvider(subject="google-subject-a")
    app.state.auth_service.provider = provider
    app.state.auth_service.credential_store = InMemoryCredentialStore(
        client_id="client-id", client_secret="client-secret"
    )
    app.state.calendar_factory = lambda settings: FakeLiveCalendar(
        FixtureCalendar(day=DAY).events()
    )
    client = TestClient(app)
    _connect(client, provider)
    return client, app


def test_places_search_requires_live_user(tmp_path) -> None:
    app, _ = _live_app(tmp_path)
    client = TestClient(app)
    created = client.post("/api/demo/session", json={})
    headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

    result = client.get("/api/places/search", params={"query": "northside"}, headers=headers)

    assert result.status_code == 400
    assert "connected Google Calendar account" in result.json()["detail"]


def test_places_search_reports_unavailable_when_not_configured(tmp_path) -> None:
    client, _ = _connected_live_client(tmp_path)

    result = client.get("/api/places/search", params={"query": "northside"})

    assert result.status_code == 503
    assert "not configured" in result.json()["detail"]


def test_places_search_trims_query_and_returns_matches(tmp_path) -> None:
    client, app = _connected_live_client(tmp_path)
    lookup = FakePlaceLookup(
        [
            PlaceRef(
                id="place-1",
                label="Northside Community Centre",
                provenance="fake",
                confirmed=False,
                storage_policy_status=StoragePolicyStatus.EPHEMERAL,
            )
        ]
    )
    app.state.place_search = lookup

    result = client.get("/api/places/search", params={"query": "  Northside  "})

    assert result.status_code == 200
    assert [place["id"] for place in result.json()] == ["place-1"]
    assert lookup.calls == [
        {"query": "Northside", "region": None, "storage_allowed": False}
    ]


def test_places_search_rejects_blank_query(tmp_path) -> None:
    client, app = _connected_live_client(tmp_path)
    app.state.place_search = FakePlaceLookup()

    result = client.get("/api/places/search", params={"query": "   "})

    assert result.status_code == 400
    assert result.json()["detail"] == "Provide a place query."
