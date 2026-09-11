"""Session lookup by ``X-Glide-Session`` must never scan the state table.

Regression coverage for F4/S3: any unauthenticated caller can send an
arbitrary session header, so the lookup has to be a bounded query on a key
derived from the session id.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.api.app import create_app
from glide.domain.models import SampleSnapshot

from tests.unit.test_dynamodb import FakeDynamoDb


class CountingDynamoDb(FakeDynamoDb):
    """FakeDynamoDb that records scan and query calls."""

    def __init__(self) -> None:
        super().__init__()
        self.scan_calls = 0
        self.query_calls = 0

    def scan(self, *args, **kwargs):
        self.scan_calls += 1
        return super().scan(*args, **kwargs)

    def query(self, *args, **kwargs):
        self.query_calls += 1
        return super().query(*args, **kwargs)


def _snapshot(
    session_id: str,
    *,
    expires_at: datetime | None = None,
) -> SampleSnapshot:
    return SampleSnapshot(
        user_id="sample-abc",
        session_id=session_id,
        day=date(2026, 9, 9),
        source_events=(),
        skipped_journeys=(),
        generation=0,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


def _store() -> tuple[CountingDynamoDb, DynamoDbStateStore]:
    client = CountingDynamoDb()
    return client, DynamoDbStateStore(client, "glide")


def test_snapshot_is_indexed_under_its_session_key() -> None:
    client, store = _store()
    snapshot = _snapshot("known-session")

    store.save_sample_snapshot(snapshot)

    keys = {item["pk"]["S"] for item in client.items["glide"].values()}
    assert keys == {"USER#sample-abc", "SESSION#known-session"}
    assert store.get_sample_snapshot_by_session("known-session") == snapshot
    assert client.scan_calls == 0


def test_unknown_session_is_a_bounded_query_without_scans() -> None:
    client, store = _store()
    store.save_sample_snapshot(_snapshot("known-session"))
    queries_before = client.query_calls

    assert store.get_sample_snapshot_by_session("unknown-session") is None

    assert client.scan_calls == 0
    assert client.query_calls == queries_before + 1


def test_expired_indexed_snapshot_is_not_returned() -> None:
    client, store = _store()
    store.save_sample_snapshot(
        _snapshot(
            "expired-session",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    assert store.get_sample_snapshot_by_session("expired-session") is None
    assert client.scan_calls == 0


def test_clear_user_removes_both_snapshot_items() -> None:
    client, store = _store()
    store.save_sample_snapshot(_snapshot("known-session"))

    store.clear_user("sample-abc")

    assert client.items["glide"] == {}


def test_request_with_unknown_session_header_never_scans() -> None:
    """The acceptance test for F4: a bad header records 0 Scan calls."""

    client, store = _store()
    store.save_sample_snapshot(_snapshot("known-session"))
    scans_before = client.scan_calls
    queries_before = client.query_calls

    with TestClient(create_app(state_store=store, run_local_worker=False)) as api:
        response = api.get(
            "/api/day",
            headers={"X-Glide-Session": "attacker-supplied"},
        )

    assert response.status_code == 404
    assert client.scan_calls == scans_before
    assert client.query_calls == queries_before + 1


def test_request_with_a_known_session_header_never_scans() -> None:
    client, store = _store()
    store.save_sample_snapshot(_snapshot("known-session"))

    with TestClient(create_app(state_store=store, run_local_worker=False)) as api:
        response = api.get(
            "/api/day",
            headers={"X-Glide-Session": "known-session"},
        )

    assert response.status_code == 200
    assert client.scan_calls == 0
