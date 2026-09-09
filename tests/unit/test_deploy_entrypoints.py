"""Deployment entrypoint tests.

``deploy/api.py`` and ``deploy/worker.py`` were previously exercised only by
manually assembled local apps. These tests drive the real Lambda factories
and handlers with fake boto3 clients, covering the production import gate,
the sample-session rebuild on a cold worker, live-user failure fencing, and
SQS record parsing.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import UTC, datetime

import boto3
import pytest
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.agent.runner import DeterministicAgentRunner
from glide.api.demo_store import DemoSessionStore
from glide.domain.models import Run, RunStatus
from glide.jobs.queue import Job
from mangum import Mangum

from tests.unit.test_amazon_location import FakePlacesClient, FakeRoutesClient
from tests.unit.test_credentials import FakeSecretsClient
from tests.unit.test_dynamodb import FakeDynamoDb
from tests.unit.test_sqs_queue import FakeSqs

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()


def _fake_boto3(monkeypatch, clients: dict[str, object]) -> list[tuple[str, object]]:
    """Point every boto3 service factory at the supplied fake and record calls."""

    calls: list[tuple[str, object]] = []

    def fake_client(service, region_name=None):
        calls.append((service, region_name))
        return clients[service]

    monkeypatch.setattr(boto3, "client", fake_client)
    return calls


def _worker_clients(dynamodb: FakeDynamoDb) -> dict[str, object]:
    return {
        "dynamodb": dynamodb,
        "sqs": FakeSqs(),
        "secretsmanager": FakeSecretsClient(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "scopes": ["https://www.googleapis.com/auth/calendar.events"],
            }
        ),
        "geo-places": FakePlacesClient(),
        "geo-routes": FakeRoutesClient(),
    }


def _worker_env(monkeypatch) -> None:
    monkeypatch.setenv("GLIDE_TABLE_NAME", "glide")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")


def test_lambda_app_uses_deployed_adapters_without_local_worker(monkeypatch) -> None:
    monkeypatch.setenv("GLIDE_TABLE_NAME", "glide-table")
    monkeypatch.setenv("GLIDE_QUEUE_URL", "https://queue.example/glide.fifo")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")

    clients: dict[str, object] = {
        "dynamodb": FakeDynamoDb(),
        "sqs": FakeSqs(),
        "secretsmanager": FakeSecretsClient(
            {"access_token": "access", "refresh_token": "refresh", "scopes": []}
        ),
        "geo-places": FakePlacesClient(),
    }
    calls = _fake_boto3(monkeypatch, clients)

    sys.modules.pop("glide.deploy.api", None)
    module = importlib.import_module("glide.deploy.api")

    assert [service for service, _ in calls] == [
        "dynamodb",
        "sqs",
        "secretsmanager",
        "geo-places",
    ]
    assert calls[-1] == ("geo-places", "eu-west-2")

    app = module.app
    assert app is not None
    assert app.state.worker is None
    assert app.state.dispatcher is None
    assert isinstance(app.state.state_store, DynamoDbStateStore)
    assert app.state.state_store._client is clients["dynamodb"]  # noqa: SLF001
    assert app.state.queue._client is clients["sqs"]  # noqa: SLF001
    assert app.state.credential_store._client is clients["secretsmanager"]  # noqa: SLF001
    assert app.state.place_search._client is clients["geo-places"]  # noqa: SLF001
    assert isinstance(module.handler, Mangum)


def test_production_import_never_builds_local_sqlite_app(monkeypatch) -> None:
    """Regression for F12: the Lambda bootstrap must not touch local storage."""

    monkeypatch.setenv("GLIDE_ENV", "production")
    original = sys.modules.get("glide.api.app")
    try:
        sys.modules.pop("glide.api.app", None)
        app_module = importlib.import_module("glide.api.app")
        assert app_module.app is None
    finally:
        if original is not None:
            sys.modules["glide.api.app"] = original
        else:
            sys.modules.pop("glide.api.app", None)


def test_worker_rebuilds_sample_session_from_shared_store(monkeypatch) -> None:
    """Regression for F9: a cold worker restores the sample from DynamoDB."""

    dynamodb = FakeDynamoDb()
    store = DynamoDbStateStore(dynamodb, "glide")
    seeding = DemoSessionStore(
        agent_runner=DeterministicAgentRunner(),
        state_store=store,
    )
    session = seeding.create(day=DAY)
    user_id = session.settings.user_id
    run_id = "run-sample-1"
    store.save_run(
        Run(
            id=run_id,
            user_id=user_id,
            trigger="sample",
            status=RunStatus.QUEUED,
            lease_revision=1,
            source_fingerprint="",
            started_at=datetime(2026, 9, 9, 8, 0, tzinfo=UTC),
        )
    )

    _worker_env(monkeypatch)
    _fake_boto3(monkeypatch, _worker_clients(dynamodb))
    worker = importlib.import_module("glide.deploy.worker")

    process = worker.build_processor()
    process(Job(id="message-1", user_id=user_id, trigger="sample", run_id=run_id))

    run = store.get_run(run_id)
    assert run is not None
    assert run.status == RunStatus.NEEDS_INPUT
    assert len(store.get_blocks(user_id)) == 1
    assert len(store.get_decisions(user_id)) == 1


def test_worker_live_job_without_settings_persists_failure(monkeypatch) -> None:
    """Live failures are fenced to a safe failed run and re-raised."""

    dynamodb = FakeDynamoDb()
    store = DynamoDbStateStore(dynamodb, "glide")
    _worker_env(monkeypatch)
    _fake_boto3(monkeypatch, _worker_clients(dynamodb))
    worker = importlib.import_module("glide.deploy.worker")

    process = worker.build_processor()
    job = Job(
        id="message-2",
        user_id="google:subject",
        trigger="schedule",
        run_id="run-live-1",
    )

    with pytest.raises(RuntimeError, match="no persisted settings"):
        process(job)

    run = store.get_run("run-live-1")
    assert run is not None
    assert run.status == RunStatus.FAILED
    assert run.safe_failure_code == "RuntimeError"


def test_worker_unknown_sample_session_fails_safely(monkeypatch) -> None:
    """A missing sample tenant becomes a safe failed run, never a silent pass."""

    dynamodb = FakeDynamoDb()
    store = DynamoDbStateStore(dynamodb, "glide")
    _worker_env(monkeypatch)
    _fake_boto3(monkeypatch, _worker_clients(dynamodb))
    worker = importlib.import_module("glide.deploy.worker")

    process = worker.build_processor()
    job = Job(
        id="message-3",
        user_id="sample-unknown",
        trigger="schedule",
        run_id="run-sample-2",
    )

    with pytest.raises(RuntimeError, match="gone"):
        process(job)

    run = store.get_run("run-sample-2")
    assert run is not None
    assert run.status == RunStatus.FAILED
    assert run.safe_failure_code == "RuntimeError"


def test_worker_handler_drives_processor_and_skips_unreadable_records(
    monkeypatch,
) -> None:
    """The Lambda handler parses SQS records and skips malformed bodies."""

    worker = importlib.import_module("glide.deploy.worker")
    processed: list[Job] = []
    worker._PROCESSOR = processed.append  # noqa: SLF001
    try:
        result = worker.handler(
            {
                "Records": [
                    {
                        "messageId": "m1",
                        "body": json.dumps(
                            {
                                "user_id": "user-1",
                                "trigger": "schedule",
                                "run_id": "run-1",
                            }
                        ),
                    },
                    {"messageId": "m2", "body": "not-json"},
                ]
            },
            None,
        )
    finally:
        worker._PROCESSOR = None  # noqa: SLF001

    assert result == {"statusCode": 200}
    assert len(processed) == 1
    job = processed[0]
    assert (job.id, job.user_id, job.trigger, job.run_id) == (
        "m1",
        "user-1",
        "schedule",
        "run-1",
    )
