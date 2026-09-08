from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    local_datetime,
    place_index,
)
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import (
    Decision,
    DecisionStatus,
    JourneyPlan,
    ManagedBlock,
    MutationOperation,
    MutationOutcome,
    MutationReceipt,
    PlanAction,
    Run,
    RunStatus,
    UserSettings,
)


class FakeDynamoDb:
    """In-memory low-level DynamoDB client for the adapter's exact surface."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, dict[str, Any]]] = {}

    def put_item(self, TableName: str, Item: dict[str, Any]) -> None:
        self._items(TableName)[self._identity(Item)] = Item

    def get_item(self, TableName: str, Key: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity(Key)
        item = self._items(TableName).get(identity)
        return {"Item": item} if item is not None else {}

    def query(
        self,
        TableName: str,
        KeyConditionExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        IndexName: str | None = None,
    ) -> dict[str, Any]:
        values = {
            key: value["S"] for key, value in ExpressionAttributeValues.items()
        }
        items = self._items(TableName).values()
        if IndexName == "user-index":
            matches = [
                item for item in items if item.get("user_pk", {}).get("S") == values[":user"]
            ]
        elif KeyConditionExpression.startswith("pk = :pk"):
            prefix = values.get(":prefix")
            matches = [
                item
                for item in items
                if item["pk"]["S"] == values[":pk"]
                and (prefix is None or item["sk"]["S"].startswith(prefix))
            ]
        else:
            raise AssertionError(f"unsupported query expression {KeyConditionExpression}")
        return {"Items": sorted(matches, key=lambda item: item["sk"]["S"])}

    def scan(self, TableName: str) -> dict[str, Any]:
        return {"Items": list(self._items(TableName).values())}

    def batch_write_item(self, RequestItems: dict[str, Any]) -> None:
        for table, requests in RequestItems.items():
            for request in requests:
                if "PutRequest" in request:
                    self.put_item(table, request["PutRequest"]["Item"])
                elif "DeleteRequest" in request:
                    key = request["DeleteRequest"]["Key"]
                    self._items(table).pop(self._identity(key), None)

    def transact_write_items(self, TransactItems: list[dict[str, Any]]) -> None:
        # Build first so a mid-sequence failure cannot leave partial state.
        staged: list[tuple[str, str, dict[str, Any]]] = []
        for transaction in TransactItems:
            if "Put" in transaction:
                staged.append(
                    ("put", transaction["Put"]["TableName"], transaction["Put"]["Item"])
                )
            elif "Delete" in transaction:
                key = transaction["Delete"]["Key"]
                staged.append(("delete", transaction["Delete"]["TableName"], key))

        touched: set[tuple[str, str, str]] = set()
        for _operation, table, value in staged:
            identity = (table, value["pk"]["S"], value["sk"]["S"])
            if identity in touched:
                raise ValueError(
                    "Transaction request cannot include multiple operations on one item"
                )
            touched.add(identity)

        for operation, table, value in staged:
            if operation == "put":
                self.put_item(table, value)
            else:
                self._items(table).pop(self._identity(value), None)

    def _items(self, table: str) -> dict[str, dict[str, Any]]:
        return self.items.setdefault(table, {})

    @staticmethod
    def _identity(item: dict[str, Any]) -> tuple[str, str]:
        return (item["pk"]["S"], item["sk"]["S"])


def _settings() -> UserSettings:
    return UserSettings.model_validate(canonical_settings(user_id="user-1"))


def _canonical_result() -> tuple[SampleWorkflow, object]:
    day = datetime(2026, 9, 9, tzinfo=UTC).date()
    calendar = FixtureCalendar(day=day)
    workflow = SampleWorkflow(
        settings=_settings(),
        travel_calendar=InMemoryTravelCalendar(),
        router=FixtureRouter(),
    )
    result = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )
    return workflow, result


def test_settings_roundtrip() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    settings = _settings()
    store.save_settings(settings)
    assert store.get_settings(settings.user_id) == settings
    assert store.get_settings("missing") is None


def test_save_result_roundtrips_every_child_record() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    _, result = _canonical_result()
    store.save_result(result)

    assert store.get_run(result.run.id) == result.run
    assert sorted(
        store.get_plans(result.run.id), key=lambda plan: plan.journey_key
    ) == sorted(result.plans, key=lambda plan: plan.journey_key)
    assert store.get_decisions(result.run.user_id) == list(result.decisions)
    assert sorted(
        store.get_receipts(result.run.id), key=lambda receipt: receipt.id
    ) == sorted(result.receipts, key=lambda receipt: receipt.id)
    assert sorted(
        store.get_blocks(result.run.user_id), key=lambda block: block.journey_key
    ) == sorted(result.travel_blocks, key=lambda block: block.journey_key)


def test_save_result_replaces_previous_blocks() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    day = datetime(2026, 9, 9, tzinfo=UTC).date()
    calendar = FixtureCalendar(day=day)
    workflow = SampleWorkflow(
        settings=_settings(),
        travel_calendar=InMemoryTravelCalendar(),
        router=FixtureRouter(),
    )
    now = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)
    first = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    store.save_result(first)
    assert len(store.get_blocks(first.run.user_id)) == 1

    # A later completed run materializes two blocks; the old one must be gone.
    calendar.move("occ_b", local_datetime(day, 10, 45), local_datetime(day, 11, 15))
    second = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    store.save_result(second)
    blocks = store.get_blocks(second.run.user_id)
    assert sorted(blocks, key=lambda block: block.journey_key) == sorted(
        second.travel_blocks, key=lambda block: block.journey_key
    )
    assert len(blocks) == 2


def test_idempotent_rerun_never_duplicates_a_block_in_one_transaction() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    day = datetime(2026, 9, 9, tzinfo=UTC).date()
    calendar = FixtureCalendar(day=day)
    workflow = SampleWorkflow(
        settings=_settings(),
        travel_calendar=InMemoryTravelCalendar(),
        router=FixtureRouter(),
    )
    now = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)
    first = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    store.save_result(first)

    # An unchanged day produces the same block; the transaction must not
    # delete and put the same item (DynamoDB rejects that).
    second = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    store.save_result(second)
    assert len(store.get_blocks(second.run.user_id)) == 1


def test_receipt_carries_ttl() -> None:
    client = FakeDynamoDb()
    store = DynamoDbStateStore(client, "glide")
    receipt = MutationReceipt(
        id="r-1",
        run_id="run-1",
        journey_key="jk",
        operation=MutationOperation.CREATE,
        provider_event_id=None,
        before_hash=None,
        after_hash=None,
        outcome=MutationOutcome.APPLIED,
        timestamp=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    store.save_run(
        Run(
            id="run-1",
            user_id="user-1",
            trigger="sample",
            status=RunStatus.COMPLETED,
            lease_revision=1,
            source_fingerprint="fp",
            started_at=datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
        )
    )
    store.save_receipts([receipt])
    item = next(
        item
        for item in client.items["glide"].values()
        if item["sk"]["S"].startswith("RECEIPT#")
    )
    assert int(item["ttl"]["N"]) > int(
        datetime(2026, 9, 9, 12, 0, tzinfo=UTC).timestamp()
    )


def test_clear_user_removes_only_that_tenant() -> None:
    client = FakeDynamoDb()
    store = DynamoDbStateStore(client, "glide")
    settings_a = UserSettings.model_validate(canonical_settings(user_id="user-a"))
    settings_b = UserSettings.model_validate(canonical_settings(user_id="user-b"))
    store.save_settings(settings_a)
    store.save_settings(settings_b)
    run = Run(
        id="run-a",
        user_id="user-a",
        trigger="sample",
        status=RunStatus.COMPLETED,
        lease_revision=1,
        source_fingerprint="fp",
        started_at=datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )
    store.save_run(run)
    store.save_plans(
        "run-a",
        [
            JourneyPlan(
                journey_key="jk",
                origin_occurrence_id="occ-a",
                destination_occurrence_id="occ-b",
                source_calendar_id="fixture-primary",
                source_etags={},
                route_estimate_id="est",
                padding_minutes=10,
                action=PlanAction.CREATE,
                reason_code="feasible",
            )
        ],
    )
    store.save_decisions(
        [
            Decision(
                id="d-1",
                user_id="user-a",
                occurrence_id="occ-b",
                journey_key="jk",
                source_revision="fp",
                reason="insufficient_time",
                calculated_facts={},
                allowed_actions=("skip_journey",),
                status=DecisionStatus.OPEN,
                version=1,
            )
        ]
    )
    store.save_blocks(
        "user-a",
        [
            ManagedBlock(
                journey_key="jk",
                provider_event_id="evt",
                start=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
                end=datetime(2026, 9, 9, 9, 30, tzinfo=UTC),
                last_applied_hash="h",
                etag="e",
                source_revision="fp",
                policy_revision=1,
            )
        ],
    )

    store.clear_user("user-a")

    assert store.get_settings("user-a") is None
    assert store.get_settings("user-b") == settings_b
    assert store.get_run("run-a") is None
    assert store.get_plans("run-a") == []
    assert store.get_decisions("user-a") == []
    assert store.get_blocks("user-a") == []


def test_get_receipts_without_run_scans_every_receipt() -> None:
    client = FakeDynamoDb()
    store = DynamoDbStateStore(client, "glide")
    _, result = _canonical_result()
    store.save_result(result)
    assert sorted(
        store.get_receipts(), key=lambda receipt: receipt.id
    ) == sorted(result.receipts, key=lambda receipt: receipt.id)


def test_save_plans_requires_a_persisted_run() -> None:
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    plan = JourneyPlan(
        journey_key="jk",
        origin_occurrence_id="occ-a",
        destination_occurrence_id="occ-b",
        source_calendar_id="fixture-primary",
        source_etags={},
        route_estimate_id="est",
        padding_minutes=10,
        action=PlanAction.CREATE,
        reason_code="feasible",
    )
    with pytest.raises(KeyError):
        store.save_plans("missing-run", [plan])
