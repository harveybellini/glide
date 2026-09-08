from __future__ import annotations

import json

from fastapi.testclient import TestClient
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.api.app import create_app
from glide.api.run_service import build_run_processor
from glide.jobs.queue import Job
from glide.jobs.sqs_queue import SqsJobQueue

from tests.unit.test_dynamodb import FakeDynamoDb
from tests.unit.test_sqs_queue import FakeSqs


def test_demo_flow_with_dynamodb_and_sqs_backends() -> None:
    """The same API works when its local backends are swapped for deployed ones."""

    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    sqs = FakeSqs()
    queue = SqsJobQueue(sqs, "https://queue.example/fifo")
    app = create_app(state_store=store, job_queue=queue, run_local_worker=False)

    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        assert created.status_code == 201
        session_id = created.json()["session"]["session_id"]
        user_id = created.json()["settings"]["user_id"]
        headers = {"X-Glide-Session": session_id}

        # Settings are persisted so the scheduled dispatcher can find tenants.
        assert store.get_settings(user_id) is not None

        queued = client.post("/api/runs", headers=headers, json={})
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        assert store.get_run(run_id) is not None

        # The SQS record mirrors what the worker Lambda will receive.
        message = sqs.messages[-1]
        body = json.loads(message["MessageBody"])
        assert body == {"user_id": user_id, "trigger": "sample", "run_id": run_id}

        processor = build_run_processor(app.state.demo_store, store)
        processor(
            Job(
                id=message["MessageDeduplicationId"],
                user_id=body["user_id"],
                trigger=body["trigger"],
                run_id=body["run_id"],
            )
        )

        result = client.get(f"/api/runs/{run_id}", headers=headers)
        assert result.status_code == 200
        assert result.json()["run"]["status"] == "needs_input"
        assert len(result.json()["travel_blocks"]) == 1
        assert len(result.json()["decisions"]) == 1

        day = client.get("/api/day", headers=headers)
        assert len(day.json()["travel_blocks"]) == 1

        reset = client.post("/api/demo/reset", headers=headers)
        assert reset.status_code == 200
        assert store.get_run(run_id) is None
        # Reset clears run state but preserves the tenant's settings and
        # snapshots a fresh, empty calendar for any worker instance.
        assert store.get_settings(user_id) is not None
        snapshot = store.get_sample_snapshot(user_id)
        assert snapshot is not None
        assert snapshot.generation == 1


def test_reprocessing_the_same_run_is_harmless() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    sqs = FakeSqs()
    queue = SqsJobQueue(sqs, "https://queue.example/fifo")
    app = create_app(state_store=store, job_queue=queue, run_local_worker=False)

    with TestClient(app) as client:
        created = client.post("/api/demo/session")
        session_id = created.json()["session"]["session_id"]
        user_id = created.json()["settings"]["user_id"]
        headers = {"X-Glide-Session": session_id}
        queued = client.post("/api/runs", headers=headers, json={}).json()
        processor = build_run_processor(app.state.demo_store, store)
        body = json.loads(sqs.messages[-1]["MessageBody"])
        job = Job(
            id="message-1",
            user_id=body["user_id"],
            trigger=body["trigger"],
            run_id=body["run_id"],
        )

        processor(job)
        processor(job)

        assert len(store.get_blocks(user_id)) == 1
        assert store.get_run(queued["run_id"]).status.value == "needs_input"
        receipts = store.get_receipts(queued["run_id"])
        creates = [r for r in receipts if r.operation.value == "create"]
        assert len(creates) == 1
