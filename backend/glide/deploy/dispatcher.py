"""EventBridge scheduled dispatcher entrypoint.

Every five minutes it scans persisted settings in bounded pages, enqueues one
check per enabled tenant, and relies on FIFO ordering plus the worker's
ownership checks for safety. It requires settings to be persisted by the API
routes, which the sample routes now do. The scan is a placeholder for an
active/due index; page size keeps each invocation bounded meanwhile.
"""

from __future__ import annotations

import os
from typing import Any

import boto3

from glide.domain.models import UserSettings
from glide.jobs.sqs_queue import SqsJobQueue


def dispatch_once(
    dynamodb,
    queue: SqsJobQueue,
    *,
    table_name: str,
    page_size: int = 100,
) -> int:
    """Scan enabled settings in bounded pages and enqueue one job each."""

    enqueued = 0
    start_key = None
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
            if settings.enabled:
                queue.enqueue(settings.user_id, "schedule")
                enqueued += 1
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            break
    return enqueued


def handler(event: dict[str, Any], context: Any = None) -> dict[str, int]:
    del event, context
    table_name = os.environ["GLIDE_TABLE_NAME"]
    queue_url = os.environ["GLIDE_QUEUE_URL"]
    queue = SqsJobQueue(boto3.client("sqs"), queue_url)
    enqueued = dispatch_once(
        boto3.client("dynamodb"),
        queue,
        table_name=table_name,
    )
    return {"enqueued": enqueued}
