from __future__ import annotations

import json
from datetime import date

from glide.adapters.fixtures import canonical_settings
from glide.deploy.dispatcher import dispatch_once
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
