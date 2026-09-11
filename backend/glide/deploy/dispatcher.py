"""EventBridge scheduled dispatcher entrypoint.

Every five minutes it scans persisted settings in bounded pages, enqueues one
check per enabled tenant, and relies on FIFO ordering plus the worker's
ownership checks for safety. It requires settings to be persisted by the API
routes, which the sample routes now do. The scan is a placeholder for an
active/due index; page size keeps each invocation bounded meanwhile.

Sample tenants (``sample-*``) are never scheduled. The public demo creates
those tenants without authentication, so scheduling them would let anonymous
traffic generate recurring work for up to the 24-hour snapshot lifetime.
Sample runs still execute on explicit user action through the worker's
deterministic sample processor.
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
from glide.jobs.sqs_queue import SqsJobQueue

DISPATCH_CURSOR_KEY = {
    "pk": {"S": "SYSTEM#DISPATCHER"},
    "sk": {"S": "SETTINGS_SCAN_CURSOR"},
}


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
    page_size: int = 100,
    max_pages: int = 10,
) -> int:
    """Scan enabled settings in bounded pages and enqueue one job each.

    Expired or inactive sample tenants are skipped so they can never generate
    model calls after their snapshot TTL lapses. Each invocation is capped at
    ``max_pages``; the next scheduled tick continues where it left off.
    """

    enqueued = 0
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
            if not settings.enabled:
                continue
            if settings.user_id.startswith("sample-"):
                # F1/S1: anonymous demo tenants must never self-schedule.
                continue
            run_id = f"run-{uuid.uuid4().hex}"
            state_store.save_run(
                Run(
                    id=run_id,
                    user_id=settings.user_id,
                    trigger="schedule",
                    status=RunStatus.QUEUED,
                    lease_revision=1,
                    source_fingerprint="",
                    started_at=datetime.now(UTC),
                )
            )
            queue.enqueue(settings.user_id, "schedule", run_id=run_id)
            enqueued += 1
        pages += 1
        start_key = response.get("LastEvaluatedKey")
        # Persist only after every job on the page has been enqueued. If the
        # invocation fails first, repeating a page is safer than losing tenants.
        _save_dispatch_cursor(dynamodb, table_name, start_key)
        if not start_key or pages >= max_pages:
            break
    return enqueued


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
    )
    return {"enqueued": enqueued}
