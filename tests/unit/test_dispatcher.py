from __future__ import annotations

import json
from datetime import date

from glide.adapters.fixtures import canonical_settings
from glide.deploy.dispatcher import dispatch_once, handler
from glide.domain.models import Run, RunStatus, SampleSnapshot, UserSettings
from glide.jobs.sqs_queue import SqsJobQueue

from tests.unit.test_sqs_queue import FakeSqs


class FakeDynamoDb:
    def __init__(self, settings: list[UserSettings]) -> None:
        self.items = [
            {
                "sk": {"S": "SETTINGS"},
                "payload": {"S": settings.model_dump_json()},
            }
            for settings in settings
        ]
        self.scan_calls: list[dict] = []
        self.put_calls: list[dict] = []
        self.cursor_item: dict | None = None

    def get_item(self, TableName, Key):
        del TableName, Key
        return {"Item": self.cursor_item} if self.cursor_item is not None else {}

    def scan(
        self,
        TableName,
        FilterExpression,
        ExpressionAttributeValues,
        Limit,
        ExclusiveStartKey=None,
    ):
        self.scan_calls.append(
            {
                "Limit": Limit,
                "ExclusiveStartKey": ExclusiveStartKey,
            }
        )
        start = ExclusiveStartKey["index"] if ExclusiveStartKey else 0
        page = self.items[start : start + Limit]
        last = start + len(page) if len(page) == Limit else None
        return {
            "Items": page,
            **({"LastEvaluatedKey": {"index": last}} if last is not None else {}),
        }

    def put_item(self, TableName, Item):
        self.put_calls.append({"TableName": TableName, "Item": Item})
        if Item["pk"]["S"] == "SYSTEM#DISPATCHER":
            self.cursor_item = Item


class CapturingStateStore:
    def __init__(self, snapshots: dict[str, SampleSnapshot] | None = None) -> None:
        self.runs: list[Run] = []
        self.snapshots = snapshots or {}

    def save_run(self, run: Run) -> None:
        self.runs.append(run)

    def get_sample_snapshot(self, user_id: str) -> SampleSnapshot | None:
        return self.snapshots.get(user_id)


def test_dispatcher_scans_bounded_pages_and_enqueues_enabled_users() -> None:
    settings = [
        UserSettings.model_validate(canonical_settings(user_id=f"user-{index}"))
        for index in range(5)
    ]
    settings[2] = settings[2].model_copy(update={"enabled": False})
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()
    state_store = CapturingStateStore()

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        page_size=2,
    )

    assert enqueued == 4
    assert [call["Limit"] for call in dynamodb.scan_calls] == [2, 2, 2]
    groups = {message["MessageGroupId"] for message in sqs.messages}
    assert groups == {"user-0", "user-1", "user-3", "user-4"}
    assert len(state_store.runs) == 4
    assert all(run.status == RunStatus.QUEUED for run in state_store.runs)
    run_ids = {run.id for run in state_store.runs}
    assert {
        json.loads(message["MessageBody"])["run_id"]
        for message in sqs.messages
    } == run_ids


def test_dispatcher_skips_expired_sample_tenants() -> None:
    active = UserSettings.model_validate(
        canonical_settings(user_id="sample-active")
    )
    expired = UserSettings.model_validate(
        canonical_settings(user_id="sample-expired")
    )
    dynamodb = FakeDynamoDb([active, expired])
    sqs = FakeSqs()
    state_store = CapturingStateStore(
        snapshots={
            "sample-active": SampleSnapshot(
                user_id="sample-active",
                session_id="session-active",
                day=date(2026, 9, 9),
                source_events=(),
                skipped_journeys=(),
                generation=0,
            )
        }
    )

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
    )

    assert enqueued == 1
    assert [run.user_id for run in state_store.runs] == ["sample-active"]
    assert {message["MessageGroupId"] for message in sqs.messages} == {
        "sample-active"
    }


def test_dispatcher_resumes_from_durable_cursor_on_the_next_invocation() -> None:
    settings = [
        UserSettings.model_validate(canonical_settings(user_id=f"user-{index}"))
        for index in range(5)
    ]
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()
    state_store = CapturingStateStore()

    first = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        page_size=2,
        max_pages=1,
    )
    second = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        page_size=2,
        max_pages=1,
    )

    assert first == 2
    assert second == 2
    assert dynamodb.scan_calls[0]["ExclusiveStartKey"] is None
    assert dynamodb.scan_calls[1]["ExclusiveStartKey"] == {"index": 2}
    assert [message["MessageGroupId"] for message in sqs.messages] == [
        "user-0",
        "user-1",
        "user-2",
        "user-3",
    ]


def test_dispatcher_handler_persists_run_row_before_enqueueing(monkeypatch) -> None:
    """The scheduled Lambda writes a queued run row, then one FIFO message."""

    settings = UserSettings.model_validate(canonical_settings(user_id="user-1"))
    dynamodb = FakeDynamoDb([settings])
    sqs = FakeSqs()
    monkeypatch.setenv("GLIDE_TABLE_NAME", "glide")
    monkeypatch.setenv("GLIDE_QUEUE_URL", "https://queue.example/fifo")

    def fake_client(service, region_name=None):
        assert region_name is None
        if service == "dynamodb":
            return dynamodb
        if service == "sqs":
            return sqs
        raise AssertionError(f"unexpected boto3 client {service}")

    monkeypatch.setattr("glide.deploy.dispatcher.boto3.client", fake_client)

    result = handler({}, None)

    assert result == {"enqueued": 1}
    run_puts = [
        call
        for call in dynamodb.put_calls
        if call["Item"]["pk"]["S"].startswith("RUN#")
    ]
    assert len(run_puts) == 1
    saved_run = json.loads(run_puts[0]["Item"]["payload"]["S"])
    assert saved_run["status"] == "queued"
    assert saved_run["user_id"] == "user-1"
    assert saved_run["id"].startswith("run-")
    assert len(sqs.messages) == 1
    body = json.loads(sqs.messages[0]["MessageBody"])
    assert body == {
        "user_id": "user-1",
        "trigger": "schedule",
        "run_id": saved_run["id"],
    }
