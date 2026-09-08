"""SQS FIFO queue adapter for the deployed API.

It implements the enqueue surface the API uses. Leasing, completion, and
retry are provided by SQS itself (visibility timeouts and the redrive policy)
and by the worker Lambda's event-source mapping, so they are not re-modeled
here. ``supersede`` cannot remove one message group from a shared FIFO queue;
the run processor's ownership check already prevents a reset tenant's stale
job from resurrecting a run, so supersede is a deliberate no-op.
"""

from __future__ import annotations

import json
import uuid
from typing import Protocol

from glide.jobs.queue import Job


class Sqs(Protocol):
    """The boto3 SQS client surface this adapter uses."""

    def send_message(
        self,
        QueueUrl: str,
        MessageBody: str,
        MessageGroupId: str,
        MessageDeduplicationId: str,
    ) -> dict[str, str]: ...


class SqsJobQueue:
    def __init__(self, client: Sqs, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def enqueue(self, user_id: str, trigger: str, run_id: str | None = None) -> Job:
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        message_id = self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(
                {"user_id": user_id, "trigger": trigger, "run_id": run_id},
                separators=(",", ":"),
            ),
            MessageGroupId=user_id,
            MessageDeduplicationId=run_id,
        )["MessageId"]
        return Job(id=message_id, user_id=user_id, trigger=trigger, run_id=run_id)

    def supersede(self, user_id: str) -> list[Job]:
        del user_id  # see module docstring: ownership check supersedes instead
        return []
