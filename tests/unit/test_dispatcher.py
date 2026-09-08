from __future__ import annotations

from glide.adapters.fixtures import canonical_settings
from glide.deploy.dispatcher import dispatch_once
from glide.domain.models import UserSettings
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


def test_dispatcher_scans_bounded_pages_and_enqueues_enabled_users() -> None:
    settings = [
        UserSettings.model_validate(canonical_settings(user_id=f"user-{index}"))
        for index in range(5)
    ]
    settings[2] = settings[2].model_copy(update={"enabled": False})
    dynamodb = FakeDynamoDb(settings)
    sqs = FakeSqs()

    enqueued = dispatch_once(
        dynamodb,
        SqsJobQueue(sqs, "https://queue.example/fifo"),
        table_name="glide",
        page_size=2,
    )

    assert enqueued == 4
    assert [call["Limit"] for call in dynamodb.scan_calls] == [2, 2, 2]
    groups = {message["MessageGroupId"] for message in sqs.messages}
    assert groups == {"user-0", "user-1", "user-3", "user-4"}
