from __future__ import annotations

import json
from typing import Any

from glide.jobs.sqs_queue import SqsJobQueue


class FakeSqs:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send_message(
        self,
        QueueUrl: str,
        MessageBody: str,
        MessageGroupId: str,
        MessageDeduplicationId: str,
    ) -> dict[str, str]:
        self.messages.append(
            {
                "QueueUrl": QueueUrl,
                "MessageBody": MessageBody,
                "MessageGroupId": MessageGroupId,
                "MessageDeduplicationId": MessageDeduplicationId,
            }
        )
        return {"MessageId": f"message-{len(self.messages)}"}


def test_enqueue_uses_fifo_group_and_deduplication() -> None:
    client = FakeSqs()
    queue = SqsJobQueue(client, "https://queue.example/fifo")

    job = queue.enqueue("user-1", "sample", run_id="run-1")

    assert job.id == "message-1"
    assert job.run_id == "run-1"
    message = client.messages[0]
    assert message["MessageGroupId"] == "user-1"
    assert message["MessageDeduplicationId"] == "run-1"
    body = json.loads(message["MessageBody"])
    assert body == {"user_id": "user-1", "trigger": "sample", "run_id": "run-1"}


def test_supersede_is_a_documented_noop() -> None:
    queue = SqsJobQueue(FakeSqs(), "https://queue.example/fifo")
    assert queue.supersede("user-1") == []
