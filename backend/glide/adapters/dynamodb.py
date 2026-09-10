"""DynamoDB state adapter for the deployed application.

It mirrors the ``SqliteStateStore`` contract exactly so the local sample and
the deployed API share one shape. One on-demand table stores settings, runs,
plans, decisions, receipts, and managed blocks; a single GSI
(``user-index`` on ``user_pk``/``user_sk``) scopes tenant cleanup. Complete
run results commit through ``transact_write_items`` so a poller can never
observe a terminal run with partial child records.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import TypeAdapter

from glide.domain.models import (
    Decision,
    JourneyPlan,
    ManagedBlock,
    MutationReceipt,
    Run,
    SampleSnapshot,
    UserSettings,
    WorkflowResult,
)

RECEIPT_TTL_DAYS = 7
TRANSACTION_ITEM_LIMIT = 100
BATCH_ITEM_LIMIT = 25
BATCH_WRITE_MAX_ATTEMPTS = 8


class DynamoDb(Protocol):
    """The low-level boto3 DynamoDB client surface this adapter uses."""

    def put_item(self, TableName: str, Item: dict[str, Any]) -> None: ...

    def get_item(
        self,
        TableName: str,
        Key: dict[str, Any],
    ) -> dict[str, Any]: ...

    def query(
        self,
        TableName: str,
        KeyConditionExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def scan(self, TableName: str, **kwargs: Any) -> dict[str, Any]: ...

    def batch_write_item(self, RequestItems: dict[str, Any]) -> dict[str, Any]: ...

    def transact_write_items(self, TransactItems: list[dict[str, Any]]) -> None: ...


def _text(value: str) -> dict[str, str]:
    return {"S": value}


def _number(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _key(pk: str, sk: str) -> dict[str, dict[str, str]]:
    return {"pk": _text(pk), "sk": _text(sk)}


class DynamoDbStateStore:
    def __init__(self, client: DynamoDb, table_name: str) -> None:
        self._client = client
        self._table = table_name

    def close(self) -> None:
        return None

    # ------------------------------------------------------------- utilities

    def _payload(self, model) -> dict[str, Any]:
        return _text(json.dumps(model.model_dump(mode="json"), separators=(",", ":")))

    def _parse(self, item: dict[str, Any] | None, model: type):
        if item is None:
            return None
        return model.model_validate_json(item["payload"]["S"])

    def _put(self, item: dict[str, Any]) -> None:
        self._client.put_item(TableName=self._table, Item=item)

    def _query(
        self,
        *,
        expression: str,
        values: dict[str, Any],
        index_name: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {
                "TableName": self._table,
                "KeyConditionExpression": expression,
                "ExpressionAttributeValues": values,
            }
            if index_name is not None:
                kwargs["IndexName"] = index_name
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = self._client.query(**kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return items

    def _scan(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {"TableName": self._table}
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = self._client.scan(**kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return items

    def _batch_write(self, requests: list[dict[str, Any]]) -> None:
        for start in range(0, len(requests), BATCH_ITEM_LIMIT):
            pending = requests[start : start + BATCH_ITEM_LIMIT]
            for attempt in range(BATCH_WRITE_MAX_ATTEMPTS):
                response = self._client.batch_write_item(
                    RequestItems={self._table: pending}
                )
                pending = (response or {}).get("UnprocessedItems", {}).get(
                    self._table, []
                )
                if not pending:
                    break
                if attempt + 1 < BATCH_WRITE_MAX_ATTEMPTS:
                    time.sleep(min(0.05 * (2**attempt), 1.0))
            if pending:
                raise RuntimeError(
                    "DynamoDB did not process all batch writes after "
                    f"{BATCH_WRITE_MAX_ATTEMPTS} attempts"
                )

    def _tenant_keys(self, user_id: str) -> list[dict[str, Any]]:
        items = self._query(
            expression="user_pk = :user",
            values={":user": _text(user_id)},
            index_name="user-index",
        )
        return [_key(item["pk"]["S"], item["sk"]["S"]) for item in items]

    # ----------------------------------------------------------------- items

    def _settings_item(self, settings: UserSettings) -> dict[str, Any]:
        return {
            "pk": _text(f"USER#{settings.user_id}"),
            "sk": _text("SETTINGS"),
            "user_pk": _text(settings.user_id),
            "user_sk": _text("SETTINGS"),
            "payload": self._payload(settings),
        }

    def _run_item(self, run: Run) -> dict[str, Any]:
        return {
            "pk": _text(f"RUN#{run.id}"),
            "sk": _text("RUN"),
            "user_pk": _text(run.user_id),
            "user_sk": _text(f"RUN#{run.id}"),
            "run_id": _text(run.id),
            "user_id": _text(run.user_id),
            "payload": self._payload(run),
        }

    def _plan_item(self, run_id: str, user_id: str, plan: JourneyPlan) -> dict[str, Any]:
        return {
            "pk": _text(f"RUN#{run_id}"),
            "sk": _text(f"PLAN#{plan.journey_key}"),
            "user_pk": _text(user_id),
            "user_sk": _text(f"RUN#{run_id}#PLAN#{plan.journey_key}"),
            "run_id": _text(run_id),
            "user_id": _text(user_id),
            "payload": self._payload(plan),
        }

    def _decision_item(self, decision: Decision) -> dict[str, Any]:
        return {
            "pk": _text(f"USER#{decision.user_id}"),
            "sk": _text(f"DECISION#{decision.id}"),
            "user_pk": _text(decision.user_id),
            "user_sk": _text(f"DECISION#{decision.id}"),
            "user_id": _text(decision.user_id),
            "payload": self._payload(decision),
        }

    def _receipt_item(self, receipt: MutationReceipt, user_id: str) -> dict[str, Any]:
        item = {
            "pk": _text(f"RUN#{receipt.run_id}"),
            "sk": _text(f"RECEIPT#{receipt.id}"),
            "user_pk": _text(user_id),
            "user_sk": _text(f"RUN#{receipt.run_id}#RECEIPT#{receipt.id}"),
            "run_id": _text(receipt.run_id),
            "user_id": _text(user_id),
            "payload": self._payload(receipt),
        }
        if receipt.timestamp.tzinfo is not None:
            expires = receipt.timestamp.astimezone(UTC) + timedelta(days=RECEIPT_TTL_DAYS)
            item["ttl"] = _number(int(expires.timestamp()))
        return item

    def _block_item(self, user_id: str, block: ManagedBlock) -> dict[str, Any]:
        return {
            "pk": _text(f"USER#{user_id}"),
            "sk": _text(f"BLOCK#{block.journey_key}"),
            "user_pk": _text(user_id),
            "user_sk": _text(f"BLOCK#{block.journey_key}"),
            "user_id": _text(user_id),
            "payload": self._payload(block),
        }

    def _snapshot_item(self, snapshot: SampleSnapshot) -> dict[str, Any]:
        return {
            "pk": _text(f"USER#{snapshot.user_id}"),
            "sk": _text("SAMPLE"),
            "user_pk": _text(snapshot.user_id),
            "user_sk": _text("SAMPLE"),
            "session_id": _text(snapshot.session_id),
            "ttl": _number(int(snapshot.expires_at.timestamp())),
            "payload": self._payload(snapshot),
        }

    # ------------------------------------------------------------- interface

    def save_settings(self, settings: UserSettings) -> None:
        self._put(self._settings_item(settings))

    def get_settings(self, user_id: str) -> UserSettings | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_key(f"USER#{user_id}", "SETTINGS"),
        )
        return self._parse(response.get("Item"), UserSettings)

    def save_run(self, run: Run) -> None:
        self._put(self._run_item(run))

    def get_run(self, run_id: str) -> Run | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_key(f"RUN#{run_id}", "RUN"),
        )
        return self._parse(response.get("Item"), Run)

    def save_plans(self, run_id: str, plans: list[JourneyPlan]) -> None:
        if not plans:
            return
        user_id = self._run_user(run_id)
        self._batch_write(
            [{"PutRequest": {"Item": self._plan_item(run_id, user_id, plan)}} for plan in plans]
        )

    def get_plans(self, run_id: str) -> list[JourneyPlan]:
        items = self._query(
            expression="pk = :pk AND begins_with(sk, :prefix)",
            values={":pk": _text(f"RUN#{run_id}"), ":prefix": _text("PLAN#")},
        )
        adapter = TypeAdapter(list[JourneyPlan])
        return adapter.validate_python(
            [json.loads(item["payload"]["S"]) for item in items]
        )

    def save_decisions(self, decisions: list[Decision]) -> None:
        if not decisions:
            return
        self._batch_write(
            [
                {"PutRequest": {"Item": self._decision_item(decision)}}
                for decision in decisions
            ]
        )

    def get_decisions(self, user_id: str) -> list[Decision]:
        items = self._query(
            expression="pk = :pk AND begins_with(sk, :prefix)",
            values={":pk": _text(f"USER#{user_id}"), ":prefix": _text("DECISION#")},
        )
        decisions = TypeAdapter(list[Decision]).validate_python(
            [json.loads(item["payload"]["S"]) for item in items]
        )
        return sorted(decisions, key=lambda decision: decision.id)

    def save_receipts(
        self,
        receipts: list[MutationReceipt],
        user_id: str = "",
    ) -> None:
        if not receipts:
            return
        owner = user_id or self._run_user(receipts[0].run_id)
        self._batch_write(
            [
                {"PutRequest": {"Item": self._receipt_item(receipt, owner)}}
                for receipt in receipts
            ]
        )

    def get_receipts(self, run_id: str | None = None) -> list[MutationReceipt]:
        if run_id is not None:
            items = self._query(
                expression="pk = :pk AND begins_with(sk, :prefix)",
                values={":pk": _text(f"RUN#{run_id}"), ":prefix": _text("RECEIPT#")},
            )
        else:
            items = [
                item
                for item in self._scan()
                if item["sk"]["S"].startswith("RECEIPT#")
            ]
        receipts = TypeAdapter(list[MutationReceipt]).validate_python(
            [json.loads(item["payload"]["S"]) for item in items]
        )
        return sorted(receipts, key=lambda receipt: receipt.id)

    def save_blocks(self, user_id: str, blocks: list[ManagedBlock]) -> None:
        requests: list[dict[str, Any]] = []
        existing = self._query(
            expression="pk = :pk AND begins_with(sk, :prefix)",
            values={":pk": _text(f"USER#{user_id}"), ":prefix": _text("BLOCK#")},
        )
        kept_keys = {f"BLOCK#{block.journey_key}" for block in blocks}
        requests.extend(
            [
                {"DeleteRequest": {"Key": _key(item["pk"]["S"], item["sk"]["S"])}}
                for item in existing
                if item["sk"]["S"] not in kept_keys
            ]
        )
        requests.extend(
            [
                {"PutRequest": {"Item": self._block_item(user_id, block)}}
                for block in blocks
            ]
        )
        if requests:
            self._batch_write(requests)

    def get_blocks(self, user_id: str) -> list[ManagedBlock]:
        items = self._query(
            expression="pk = :pk AND begins_with(sk, :prefix)",
            values={":pk": _text(f"USER#{user_id}"), ":prefix": _text("BLOCK#")},
        )
        return TypeAdapter(list[ManagedBlock]).validate_python(
            [json.loads(item["payload"]["S"]) for item in items]
        )

    def save_sample_snapshot(self, snapshot: SampleSnapshot) -> None:
        self._put(self._snapshot_item(snapshot))

    def get_sample_snapshot(self, user_id: str) -> SampleSnapshot | None:
        response = self._client.get_item(
            TableName=self._table,
            Key=_key(f"USER#{user_id}", "SAMPLE"),
        )
        return self._active_snapshot(self._parse(response.get("Item"), SampleSnapshot))

    def get_sample_snapshot_by_session(self, session_id: str) -> SampleSnapshot | None:
        for item in self._scan():
            if (
                item["sk"]["S"] == "SAMPLE"
                and item.get("session_id", {}).get("S") == session_id
            ):
                return self._active_snapshot(self._parse(item, SampleSnapshot))
        return None

    @staticmethod
    def _active_snapshot(snapshot: SampleSnapshot | None) -> SampleSnapshot | None:
        if snapshot is None or snapshot.expires_at <= datetime.now(UTC):
            return None
        return snapshot

    def get_latest_run(self, user_id: str) -> Run | None:
        items = self._query(
            expression="user_pk = :user",
            values={":user": _text(user_id)},
            index_name="user-index",
        )
        runs = [
            self._parse(item, Run)
            for item in items
            if item["sk"]["S"].startswith("RUN#")
        ]
        return max(runs, key=lambda run: run.started_at, default=None)

    def get_user_receipts(self, user_id: str) -> list[MutationReceipt]:
        items = self._query(
            expression="user_pk = :user",
            values={":user": _text(user_id)},
            index_name="user-index",
        )
        receipts = TypeAdapter(list[MutationReceipt]).validate_python(
            [
                json.loads(item["payload"]["S"])
                for item in items
                if "#RECEIPT#" in item["user_sk"]["S"]
            ]
        )
        return sorted(receipts, key=lambda receipt: receipt.id)

    def save_result(self, result: WorkflowResult) -> None:
        """Persist a complete result in one DynamoDB transaction."""

        transactions: list[dict[str, Any]] = [
            {"Put": {"TableName": self._table, "Item": self._run_item(result.run)}}
        ]
        transactions.extend(
            {
                "Put": {
                    "TableName": self._table,
                    "Item": self._plan_item(result.run.id, result.run.user_id, plan),
                }
            }
            for plan in result.plans
        )
        transactions.extend(
            {
                "Put": {
                    "TableName": self._table,
                    "Item": self._decision_item(decision),
                }
            }
            for decision in result.decisions
        )
        transactions.extend(
            {
                "Put": {
                    "TableName": self._table,
                    "Item": self._receipt_item(receipt, result.run.user_id),
                }
            }
            for receipt in result.receipts
        )
        existing_blocks = self._query(
            expression="pk = :pk AND begins_with(sk, :prefix)",
            values={
                ":pk": _text(f"USER#{result.run.user_id}"),
                ":prefix": _text("BLOCK#"),
            },
        )
        kept_block_keys = {f"BLOCK#{block.journey_key}" for block in result.travel_blocks}
        transactions.extend(
            {
                "Delete": {
                    "TableName": self._table,
                    "Key": _key(item["pk"]["S"], item["sk"]["S"]),
                }
            }
            for item in existing_blocks
            if item["sk"]["S"] not in kept_block_keys
        )
        transactions.extend(
            {
                "Put": {
                    "TableName": self._table,
                    "Item": self._block_item(result.run.user_id, block),
                }
            }
            for block in result.travel_blocks
        )
        if len(transactions) > TRANSACTION_ITEM_LIMIT:
            raise ValueError(
                f"result needs {len(transactions)} transaction items, over the "
                f"{TRANSACTION_ITEM_LIMIT} limit"
            )
        self._client.transact_write_items(TransactItems=transactions)

    def clear_user(self, user_id: str) -> None:
        keys = self._tenant_keys(user_id)
        self._batch_write([{"DeleteRequest": {"Key": key}} for key in keys])

    def _run_user(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"no persisted run {run_id}")
        return run.user_id
