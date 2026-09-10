from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from glide.api.app import app
from glide.domain.models import Run, RunStatus

TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.NEEDS_INPUT.value,
    RunStatus.FAILED.value,
    RunStatus.SUPERSEDED.value,
    RunStatus.PAUSED.value,
}


def wait_for_run(client: TestClient, headers: dict[str, str], run_id: str) -> dict:
    deadline = time.time() + 5
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        if body["run"]["status"] in TERMINAL_STATUSES:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal status in time")


def test_sample_session_workflow_over_http() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        assert created.status_code == 201
        session_id = created.json()["session"]["session_id"]
        sample_date = created.json()["sample_date"]
        headers = {"X-Glide-Session": session_id}

        day = client.get("/api/day", headers=headers)
        assert day.status_code == 200
        assert day.json()["date"] == created.json()["sample_date"]
        assert len(day.json()["source_events"]) == 3
        assert day.json()["travel_blocks"] == []

        queued = client.post("/api/runs", headers=headers, json={"trigger": "sample"})
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        assert queued.json()["status"] == "queued"

        body = wait_for_run(client, headers, run_id)
        assert body["run"]["status"] == "needs_input"
        assert len(body["travel_blocks"]) == 1
        assert len(body["decisions"]) == 1
        assert body["decisions"][0]["calculated_facts"]["shortfall_seconds"] == 600
        refreshed_day = client.get("/api/day", headers=headers)
        assert refreshed_day.status_code == 200
        assert len(refreshed_day.json()["travel_blocks"]) == 1
        moved = client.patch(
            "/api/demo/events/occ_b",
            headers=headers,
            json={
                "start": f"{sample_date}T10:45:00+01:00",
                "end": f"{sample_date}T11:15:00+01:00",
            },
        )
        assert moved.status_code == 200
        assert moved.json()["start"] == f"{sample_date}T09:45:00Z"

        second = client.post("/api/runs", headers=headers, json={})
        second_id = second.json()["run_id"]
        second_body = wait_for_run(client, headers, second_id)
        assert second_body["run"]["status"] == "completed"
        assert len(second_body["travel_blocks"]) == 2
        assert second_body["decisions"] == []

        activity = client.get("/api/activity", headers=headers)
        assert activity.status_code == 200
        assert activity.json()["receipts"]

        reset = client.post("/api/demo/reset", headers=headers)
        assert reset.status_code == 200
        assert client.get("/api/activity", headers=headers).json()["receipts"] == []
        assert client.get(f"/api/runs/{second_id}", headers=headers).status_code == 404


def test_skip_decision_persists_across_runs() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        session_id = created.json()["session"]["session_id"]
        headers = {"X-Glide-Session": session_id}
        first = client.post("/api/runs", headers=headers, json={}).json()
        body = wait_for_run(client, headers, first["run_id"])
        decision_id = body["decisions"][0]["id"]

        resolved = client.post(
            f"/api/decisions/{decision_id}/resolve",
            headers=headers,
            json={"action": "skip_journey"},
        )
        assert resolved.json()["decision"]["status"] == "resolved"
        auto_run_id = resolved.json()["run_id"]
        assert auto_run_id is not None

        # The answer triggers a fresh run: the skipped journey stays skipped.
        auto_body = wait_for_run(client, headers, auto_run_id)
        assert auto_body["run"]["status"] == "completed"
        assert len(auto_body["travel_blocks"]) == 1
        assert auto_body["decisions"] == []

        # A later manual run remains idempotent.
        second = client.post("/api/runs", headers=headers, json={}).json()
        second_body = wait_for_run(client, headers, second["run_id"])
        assert len(second_body["travel_blocks"]) == 1
        assert second_body["decisions"] == []


def test_queued_run_is_persisted_before_completion() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        session_id = created.json()["session"]["session_id"]
        headers = {"X-Glide-Session": session_id}

        queued = client.post("/api/runs", headers=headers, json={})
        run_id = queued.json()["run_id"]

        stored = app.state.state_store.get_run(run_id)
        assert stored is not None
        assert stored.status == RunStatus.QUEUED or stored.status in (
            RunStatus.COMPLETED,
            RunStatus.NEEDS_INPUT,
        )
        wait_for_run(client, headers, run_id)


def test_failed_job_persists_a_failed_run() -> None:
    with TestClient(app):
        app.state.worker.stop()
        state_store = app.state.state_store
        queue = app.state.queue
        run_id = f"run-{uuid.uuid4().hex}"
        state_store.save_run(
            Run(
                id=run_id,
                user_id="sample-missing-user",
                trigger="sample",
                status=RunStatus.QUEUED,
                lease_revision=1,
                source_fingerprint="",
                started_at=datetime.now(UTC),
            )
        )
        job = queue.enqueue("sample-missing-user", "sample", run_id=run_id)

        deadline = time.time() + 2
        while time.time() < deadline and queue.get(job.id).status == "queued":
            assert app.state.worker.process_one() is True

        assert queue.get(job.id).status != "queued"
        failed = state_store.get_run(run_id)
        assert failed is not None
        assert failed.status == RunStatus.FAILED
        assert failed.safe_failure_code == "RuntimeError"


def test_live_job_without_processor_fails_explicitly() -> None:
    with TestClient(app):
        app.state.worker.stop()
        state_store = app.state.state_store
        queue = app.state.queue
        run_id = f"run-{uuid.uuid4().hex}"
        state_store.save_run(
            Run(
                id=run_id,
                user_id="google:no-processor",
                trigger="live",
                status=RunStatus.QUEUED,
                lease_revision=1,
                source_fingerprint="",
                started_at=datetime.now(UTC),
            )
        )
        job = queue.enqueue("google:no-processor", "live", run_id=run_id)

        deadline = time.time() + 2
        while time.time() < deadline and queue.get(job.id).status == "queued":
            assert app.state.worker.process_one() is True

        failed = state_store.get_run(run_id)
        assert failed is not None
        assert failed.status == RunStatus.FAILED
        assert failed.safe_failure_code == "LiveProcessorUnavailable"


def test_settings_patch_updates_fields_independently() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        padding_only = client.patch(
            "/api/settings",
            headers=headers,
            json={"padding_minutes": 15},
        )
        assert padding_only.status_code == 200
        assert padding_only.json()["padding_minutes"] == 15
        assert padding_only.json()["earliest_departure"] == "06:00:00"

        departure_only = client.patch(
            "/api/settings",
            headers=headers,
            json={"earliest_departure": "07:30"},
        )
        assert departure_only.status_code == 200
        assert departure_only.json()["padding_minutes"] == 15
        assert departure_only.json()["earliest_departure"] == "07:30:00"

        invalid = client.patch(
            "/api/settings",
            headers=headers,
            json={"padding_minutes": 61},
        )
        assert invalid.status_code == 422


def test_settings_patch_accepts_enabled_time_zone_and_start_place() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        patched = client.patch(
            "/api/settings",
            headers=headers,
            json={
                "enabled": True,
                "time_zone": "America/New_York",
                "start_place": {
                    "id": "place_home",
                    "label": "Home",
                    "provenance": "test fixture",
                    "confirmed": True,
                    "storage_policy_status": "ephemeral",
                },
            },
        )

        assert patched.status_code == 200
        body = patched.json()
        assert body["enabled"] is True
        assert body["time_zone"] == "America/New_York"
        assert body["start_place"]["id"] == "place_home"


def test_settings_patch_rejects_malformed_departure_time() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        response = client.patch(
            "/api/settings",
            headers=headers,
            json={"earliest_departure": "25:99"},
        )

        assert response.status_code == 400
        assert "24-hour time" in response.json()["detail"]


def test_day_rejects_a_different_sample_date() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}
        sample_date = created.json()["sample_date"]
        other = (date.fromisoformat(sample_date) + timedelta(days=1)).isoformat()

        response = client.get(
            "/api/day",
            params={"requested_date": other},
            headers=headers,
        )

        assert response.status_code == 400


def test_editing_unknown_sample_event_is_404() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        response = client.patch(
            "/api/demo/events/nope",
            headers=headers,
            json={
                "start": "2026-09-09T09:00:00Z",
                "end": "2026-09-09T09:30:00Z",
                "location": None,
            },
        )

        assert response.status_code == 404


def test_resolving_unknown_decision_is_404() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        response = client.post(
            "/api/decisions/missing/resolve",
            headers=headers,
            json={"action": "skip_journey"},
        )

        assert response.status_code == 404


def test_sample_resolution_only_supports_skip_journey() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}
        queued = client.post("/api/runs", headers=headers, json={}).json()
        wait_for_run(client, headers, queued["run_id"])

        decisions = client.get("/api/decisions", headers=headers).json()["decisions"]
        assert decisions

        rejected = client.post(
            f"/api/decisions/{decisions[0]['id']}/resolve",
            headers=headers,
            json={"action": "correct_location"},
        )

        assert rejected.status_code == 400
        assert "skip_journey" in rejected.json()["detail"]


def test_pause_and_resume_sample_automation() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        headers = {"X-Glide-Session": created.json()["session"]["session_id"]}

        paused = client.post("/api/pause", headers=headers)
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False

        resumed = client.post("/api/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True


def test_run_result_is_inaccessible_to_another_tenant() -> None:
    with TestClient(app) as client:
        first = client.post("/api/demo/session").json()
        first_headers = {"X-Glide-Session": first["session"]["session_id"]}
        queued = client.post("/api/runs", headers=first_headers, json={}).json()
        wait_for_run(client, first_headers, queued["run_id"])

        second = client.post("/api/demo/session").json()
        second_headers = {"X-Glide-Session": second["session"]["session_id"]}
        cross = client.get(
            f"/api/runs/{queued['run_id']}",
            headers=second_headers,
        )
        assert cross.status_code == 404


def test_padding_change_updates_blocks_on_recheck() -> None:
    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        session_id = created.json()["session"]["session_id"]
        sample_date = created.json()["sample_date"]
        headers = {"X-Glide-Session": session_id}

        first = client.post("/api/runs", headers=headers, json={}).json()
        first_body = wait_for_run(client, headers, first["run_id"])
        assert first_body["travel_blocks"][0]["start"] == (
            f"{sample_date}T09:25:00Z"
        )

        patched = client.patch(
            "/api/settings",
            headers=headers,
            json={"padding_minutes": 15},
        )
        assert patched.status_code == 200

        second = client.post("/api/runs", headers=headers, json={}).json()
        second_body = wait_for_run(client, headers, second["run_id"])
        assert second_body["travel_blocks"][0]["start"] == (
            f"{sample_date}T09:20:00Z"
        )
        assert len(second_body["travel_blocks"]) == 1


def test_background_schedule_updates_without_a_recheck_request(tmp_path) -> None:
    from glide.adapters.sqlite import SqliteStateStore
    from glide.api.app import create_app

    store = SqliteStateStore(tmp_path / "background.db")
    scheduled_app = create_app(state_store=store, schedule_interval=0.05)
    with TestClient(scheduled_app) as client:
        created = client.post("/api/demo/session")
        session_id = created.json()["session"]["session_id"]
        sample_date = created.json()["sample_date"]
        headers = {"X-Glide-Session": session_id}

        first = client.post("/api/runs", headers=headers, json={}).json()
        first_body = wait_for_run(client, headers, first["run_id"])
        assert len(first_body["travel_blocks"]) == 1

        # Edit the source event directly; no recheck request follows. The
        # scheduler should reconcile within one interval plus processing.
        moved = client.patch(
            "/api/demo/events/occ_b",
            headers=headers,
            json={
                "start": f"{sample_date}T10:45:00+01:00",
                "end": f"{sample_date}T11:15:00+01:00",
            },
        )
        assert moved.status_code == 200

        deadline = time.time() + 5
        blocks = []
        while time.time() < deadline:
            day = client.get("/api/day", headers=headers).json()
            blocks = day["travel_blocks"]
            if len(blocks) == 2:
                break
            time.sleep(0.05)

        assert len(blocks) == 2
        starts = sorted(block["start"] for block in blocks)
        assert starts == [
            f"{sample_date}T09:10:00Z",
            f"{sample_date}T10:20:00Z",
        ]
    store.close()
