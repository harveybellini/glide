"""EventBridge scheduled dispatcher entrypoint.

Every five minutes it scans persisted settings in bounded pages and enqueues a
check for each tenant that is watching and due. The scan is a placeholder for
an active/due index; page size keeps each invocation bounded meanwhile.

Two cost ceilings keep anonymous demo traffic from creating recurring work
with no bound:

* a tenant is only enqueued when its own ``background_interval_minutes`` has
  elapsed since the last enqueue, and
* at most ``max_sample_schedules`` new sample tenants are enqueued per tick,
  so a burst of demo sessions cannot fan every one of them out at once.

Sample runs stay on the worker's deterministic processor, so a scheduled
sample never reaches Bedrock or Amazon Location. The interval is therefore a
ceiling on queue and DynamoDB writes, not on model spend.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3

from glide.adapters.dynamodb import DynamoDbStateStore
from glide.adapters.interfaces import StateStore
from glide.domain.models import Run, RunStatus, UserSettings
from glide.jobs.schedule_state import (
    DynamoScheduleStateStore,
    ScheduleStateStore,
    is_due,
    next_bucket,
)
from glide.jobs.sqs_queue import SqsJobQueue

DISPATCH_CURSOR_KEY = {
    "pk": {"S": "SYSTEM#DISPATCHER"},
    "sk": {"S": "SETTINGS_SCAN_CURSOR"},
}

# A single anonymous sample can be polled at most four times an hour, and a
# burst of new demo sessions cannot enqueue more than this many in one tick.
# Both bounds are enforced here, not in the sample store, so a misconfigured
# client cannot raise its own allowance.
DEFAULT_SAMPLE_INTERVAL_MINUTES = 15
MAX_SAMPLE_SCHEDULES_PER_TICK = 3


def _load_dispatch_cursor(dynamodb, table_name: str) -> dict[str, Any] | None:
    response = dynamodb.get_item(TableName=table_name, Key=DISPATCH_CURSOR_KEY)
    item = response.get("Item")
    if not item:
        return None
    cursor = json.loads(item["cursor"]["S"])
    return cursor if isinstance(cursor, dict) else None


def _save_dispatch_cursor(
    dynamodb,
    table_name: str,
    cursor: dict[str, Any] | None,
) -> None:
    dynamodb.put_item(
        TableName=table_name,
        Item={
            **DISPATCH_CURSOR_KEY,
            "cursor": {"S": json.dumps(cursor, separators=(",", ":"))},
        },
    )


def dispatch_once(
    dynamodb,
    queue: SqsJobQueue,
    state_store: StateStore,
    *,
    table_name: str,
    schedule_store: ScheduleStateStore,
    page_size: int = 100,
    max_pages: int = 10,
    max_sample_schedules: int = MAX_SAMPLE_SCHEDULES_PER_TICK,
    now: datetime | None = None,
) -> int:
    """Scan watching settings in bounded pages and enqueue the due ones.

    Expired or inactive sample tenants are skipped so they can never generate
    work after their snapshot TTL lapses. Each invocation is capped at
    ``max_pages``; the next scheduled tick continues where it left off.
    """

    tick = now or datetime.now(UTC)
    enqueued = 0
    sample_enqueued = 0
    # Due samples are collected first and enqueued after the scan, so the
    # per-tick cap rotates across sessions instead of always favouring whoever
    # happens to sort first in the table.
    pending_samples: list[tuple[datetime | None, UserSettings]] = []
    start_key = _load_dispatch_cursor(dynamodb, table_name)
    pages = 0
    while True:
        response = dynamodb.scan(
            TableName=table_name,
            FilterExpression="sk = :settings",
            ExpressionAttributeValues={":settings": {"S": "SETTINGS"}},
            Limit=page_size,
            **({"ExclusiveStartKey": start_key} if start_key else {}),
        )
        for item in response.get("Items", []):
            settings = UserSettings.model_validate_json(item["payload"]["S"])
            if not settings.enabled or not settings.background_check:
                continue
            is_sample = settings.user_id.startswith("sample-")
            if is_sample:
                if not _sample_is_active(state_store, settings, tick):
                    continue
                interval = max(
                    settings.background_interval_minutes,
                    DEFAULT_SAMPLE_INTERVAL_MINUTES,
                )
            else:
                interval = settings.background_interval_minutes
            previous = schedule_store.get(settings.user_id)
            if not is_due(previous, interval_minutes=interval, now=tick):
                continue
            if is_sample:
                pending_samples.append(
                    (
                        previous.last_scheduled_at if previous is not None else None,
                        settings,
                    )
                )
                continue
            _enqueue_check(settings, tick, state_store, schedule_store, queue)
            enqueued += 1
        pages += 1
        start_key = response.get("LastEvaluatedKey")
        # Persist only after every job on the page has been enqueued. If the
        # invocation fails first, repeating a page is safer than losing tenants.
        _save_dispatch_cursor(dynamodb, table_name, start_key)
        if not start_key or pages >= max_pages:
            break
    # Least-recently-scheduled first, with never-scheduled sessions ahead of
    # that, so a run of demo sessions cannot starve the older ones.
    pending_samples.sort(
        key=lambda entry: (
            entry[0] is not None,
            entry[0] or tick,
        )
    )
    while pending_samples and sample_enqueued < max_sample_schedules:
        _, settings = pending_samples.pop(0)
        _enqueue_check(settings, tick, state_store, schedule_store, queue)
        enqueued += 1
        sample_enqueued += 1
    return enqueued


def _enqueue_check(
    settings: UserSettings,
    tick: datetime,
    state_store: StateStore,
    schedule_store: ScheduleStateStore,
    queue: SqsJobQueue,
) -> None:
    """Advance the schedule pointer, persist the run row, then enqueue it."""

    schedule_store.save(
        settings.user_id,
        next_bucket(
            schedule_store.get(settings.user_id),
            last_viewed_at=settings.last_viewed_at,
            now=tick,
        ),
    )
    run_id = f"run-{uuid.uuid4().hex}"
    state_store.save_run(
        Run(
            id=run_id,
            user_id=settings.user_id,
            trigger="schedule",
            status=RunStatus.QUEUED,
            lease_revision=1,
            source_fingerprint="",
            started_at=tick,
        )
    )
    queue.enqueue(settings.user_id, "schedule", run_id=run_id)


def _sample_is_active(state_store: StateStore, settings: UserSettings, now: datetime) -> bool:
    """Anonymous demo tenants stop generating work after their 24-hour TTL.

    The snapshot is the durable record of that lifetime. A missing or expired
    snapshot means there is nothing left to reconcile, so the settings row -
    which is never deleted by TTL - must not keep scheduling.
    """

    snapshot = state_store.get_sample_snapshot(settings.user_id)
    return snapshot is not None and snapshot.expires_at > now


def handler(event: dict[str, Any], context: Any = None) -> dict[str, int]:
    del event, context
    table_name = os.environ["GLIDE_TABLE_NAME"]
    queue_url = os.environ["GLIDE_QUEUE_URL"]
    queue = SqsJobQueue(boto3.client("sqs"), queue_url)
    dynamodb = boto3.client("dynamodb")
    enqueued = dispatch_once(
        dynamodb,
        queue,
        DynamoDbStateStore(dynamodb, table_name),
        table_name=table_name,
        schedule_store=DynamoScheduleStateStore(dynamodb, table_name),
    )
    return {"enqueued": enqueued}
