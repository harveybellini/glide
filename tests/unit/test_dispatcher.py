from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from glide.adapters.fixtures import canonical_settings
from glide.deploy.dispatcher import dispatch_once, handler
from glide.domain.models import Run, RunStatus, SampleSnapshot, UserSettings
from glide.jobs.schedule_state import (
    InMemoryScheduleStateStore,
    ScheduleState,
)
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


def test_dispatcher_scans_bounded_pages_and_enqueues_watching_users() -> None:
    settings = [
        UserSettings.model_validate(canonical_settings(user_id=f"user-{index}"))
        for index in range(5)
    ]
    settings[2] = settings[2].model_copy(update={"enabled": False})
    settings = [
        item.model_copy(update={"background_check": True}) for item in settings
    ]
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()
    state_store = CapturingStateStore()
    schedule_store = InMemoryScheduleStateStore()

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
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


def test_dispatcher_leaves_users_that_are_not_watching_alone() -> None:
    """A one-off check is not consent to keep polling in the background."""

    paused = UserSettings.model_validate(
        canonical_settings(user_id="user-paused")
    ).model_copy(update={"enabled": False, "background_check": True})
    single_check = UserSettings.model_validate(
        canonical_settings(user_id="user-single-check")
    ).model_copy(update={"enabled": True, "background_check": False})
    dynamodb = FakeDynamoDb([paused, single_check])
    sqs = FakeSqs()
    state_store = CapturingStateStore()
    schedule_store = InMemoryScheduleStateStore()

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
    )

    assert enqueued == 0
    assert state_store.runs == []
    assert sqs.messages == []


def test_dispatcher_schedules_active_watching_samples() -> None:
    """The public demo may schedule, but only within its 24-hour lifetime."""

    active = UserSettings.model_validate(
        canonical_settings(user_id="sample-active")
    ).model_copy(update={"background_check": True, "enabled": True})
    expired = UserSettings.model_validate(
        canonical_settings(user_id="sample-expired")
    ).model_copy(update={"background_check": True, "enabled": True})
    live = UserSettings.model_validate(
        canonical_settings(user_id="user-live")
    ).model_copy(update={"background_check": True})
    dynamodb = FakeDynamoDb([active, expired, live])
    sqs = FakeSqs()
    now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    state_store = CapturingStateStore(
        snapshots={
            "sample-active": SampleSnapshot(
                user_id="sample-active",
                session_id="session-active",
                day=date(2026, 9, 9),
                source_events=(),
                skipped_journeys=(),
                generation=0,
                expires_at=now + timedelta(hours=12),
            ),
            "sample-expired": SampleSnapshot(
                user_id="sample-expired",
                session_id="session-expired",
                day=date(2026, 9, 9),
                source_events=(),
                skipped_journeys=(),
                generation=0,
                expires_at=now - timedelta(hours=1),
            ),
        }
    )
    schedule_store = InMemoryScheduleStateStore()

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        now=now,
    )

    assert enqueued == 2
    # Live tenants enqueue immediately; samples are flushed after the scan so
    # the per-tick cap can rotate fairly.
    assert sorted(run.user_id for run in state_store.runs) == [
        "sample-active",
        "user-live",
    ]
    assert {message["MessageGroupId"] for message in sqs.messages} == {
        "sample-active",
        "user-live",
    }


def test_dispatcher_respects_the_interval_ceiling() -> None:
    settings = UserSettings.model_validate(
        canonical_settings(user_id="sample-interval")
    ).model_copy(
        update={
            "background_check": True,
            "background_interval_minutes": 15,
        }
    )
    dynamodb = FakeDynamoDb([settings])
    sqs = FakeSqs()
    state_store = CapturingStateStore(
        snapshots={
            "sample-interval": SampleSnapshot(
                user_id="sample-interval",
                session_id="session-interval",
                day=date(2026, 9, 9),
                source_events=(),
                skipped_journeys=(),
                generation=0,
            )
        }
    )
    schedule_store = InMemoryScheduleStateStore()
    start = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    first = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        now=start,
    )
    too_soon = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        now=start + timedelta(minutes=14),
    )
    due_again = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        now=start + timedelta(minutes=15),
    )

    assert (first, too_soon, due_again) == (1, 0, 1)
    state = schedule_store.get("sample-interval")
    assert state is not None
    assert state.scheduled_since_view == 2


def test_dispatcher_caps_new_sample_schedules_per_tick() -> None:
    """A burst of demo sessions cannot fan out into unbounded queue work."""

    settings = [
        UserSettings.model_validate(
            canonical_settings(user_id=f"sample-{index}")
        ).model_copy(update={"background_check": True})
        for index in range(5)
    ]
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()
    now = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    state_store = CapturingStateStore(
        snapshots={
            f"sample-{index}": SampleSnapshot(
                user_id=f"sample-{index}",
                session_id=f"session-{index}",
                day=date(2026, 9, 9),
                source_events=(),
                skipped_journeys=(),
                generation=0,
                expires_at=now + timedelta(hours=24),
            )
            for index in range(5)
        }
    )
    schedule_store = InMemoryScheduleStateStore()

    per_tick: list[int] = []
    for tick in range(10):
        per_tick.append(
            dispatch_once(
                dynamodb,
                SqsJobQueue(sqs, "https://queue.example/fifo"),
                state_store,
                table_name="glide",
                schedule_store=schedule_store,
                max_sample_schedules=3,
                now=now + timedelta(minutes=15 * tick),
            )
        )

    assert all(0 < count <= 3 for count in per_tick)
    # Least-recently-scheduled first: every session gets its turn rather than
    # the same three winning every tick.
    assert {run.user_id for run in state_store.runs} == {
        f"sample-{index}" for index in range(5)
    }


def test_dispatcher_resumes_from_durable_cursor_on_the_next_invocation() -> None:
    settings = [
        UserSettings.model_validate(canonical_settings(user_id=f"user-{index}"))
        for index in range(5)
    ]
    settings = [
        item.model_copy(update={"background_check": True}) for item in settings
    ]
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()
    state_store = CapturingStateStore()
    schedule_store = InMemoryScheduleStateStore()
    # The previous tick scheduled the first page; this invocation resumes from
    # the durable cursor and picks up the next one.
    seeded_at = datetime.now(UTC)
    for user_id in ("user-0", "user-1"):
        schedule_store.save(
            user_id,
            ScheduleState(
                last_scheduled_at=seeded_at,
                scheduled_since_view=1,
            ),
        )

    skipped_page = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        page_size=2,
        max_pages=1,
    )
    resumed_page = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        page_size=2,
        max_pages=1,
    )
    tail_page = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        state_store,
        table_name="glide",
        schedule_store=schedule_store,
        page_size=2,
        max_pages=1,
    )

    assert skipped_page == 0
    assert resumed_page == 2
    assert tail_page == 1
    assert dynamodb.scan_calls[0]["ExclusiveStartKey"] is None
    assert dynamodb.scan_calls[1]["ExclusiveStartKey"] == {"index": 2}
    assert [message["MessageGroupId"] for message in sqs.messages] == [
        "user-2",
        "user-3",
        "user-4",
    ]


def test_dispatcher_handler_persists_run_row_before_enqueueing(monkeypatch) -> None:
    """The scheduled Lambda writes a queued run row, then one FIFO message."""

    settings = UserSettings.model_validate(
        canonical_settings(user_id="user-1")
    ).model_copy(update={"background_check": True})
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
