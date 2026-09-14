"""Durable per-tenant schedule pointers for background checks.

The dispatcher must be able to answer one question cheaply, on every tick, for
every enabled tenant: "is this tenant due for a scheduled check?". Re-reading
run history (a growing list) would make the cost of the answer grow with the
account's age, so the answer lives in one small pointer item per tenant:

``pk=USER#<id>`` / ``sk=SCHEDULE#STATE``

The pointer records when the last check was enqueued and how many checks were
enqueued since the owner last looked at their day. It is bookkeeping only - a
lost pointer costs at most one extra scheduled check, never the tenant's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


@dataclass(frozen=True)
class ScheduleState:
    last_scheduled_at: datetime | None = None
    scheduled_since_view: int = 0


class ScheduleStateStore(Protocol):
    def get(self, user_id: str) -> ScheduleState | None: ...

    def save(self, user_id: str, state: ScheduleState) -> None: ...


def is_due(
    state: ScheduleState | None,
    *,
    interval_minutes: int,
    now: datetime,
) -> bool:
    """Whether a tenant may be enqueued again at ``now``."""

    if state is None or state.last_scheduled_at is None:
        return True
    return state.last_scheduled_at + timedelta(minutes=interval_minutes) <= now


def next_bucket(
    state: ScheduleState | None,
    *,
    last_viewed_at: datetime | None,
    now: datetime,
) -> ScheduleState:
    """Advance the pointer, keeping the "while you were away" count honest.

    The count starts over when the owner has viewed the day since the previous
    scheduled check; otherwise it accumulates across dispatcher ticks.
    """

    previous_seen = (
        state is not None
        and state.last_scheduled_at is not None
        and last_viewed_at is not None
        and state.last_scheduled_at <= last_viewed_at
    )
    count = 1 if state is None or previous_seen else state.scheduled_since_view + 1
    return ScheduleState(last_scheduled_at=now, scheduled_since_view=count)


class DynamoScheduleStateStore:
    """Schedule pointers in the same DynamoDB table as everything else."""

    def __init__(self, client, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    @staticmethod
    def _key(user_id: str) -> dict[str, dict[str, str]]:
        return {
            "pk": {"S": f"USER#{user_id}"},
            "sk": {"S": "SCHEDULE#STATE"},
        }

    @staticmethod
    def _text(value: str) -> dict[str, str]:
        return {"S": value}

    def get(self, user_id: str) -> ScheduleState | None:
        item = self._client.get_item(
            TableName=self._table_name,
            Key=self._key(user_id),
        ).get("Item")
        if not item:
            return None
        raw_time = item.get("last_scheduled_at", {}).get("S")
        if not raw_time:
            return None
        try:
            scheduled_at = datetime.fromisoformat(raw_time)
            count = int(item.get("scheduled_since_view", {}).get("N", "0"))
        except (TypeError, ValueError):
            return None
        return ScheduleState(
            last_scheduled_at=scheduled_at,
            scheduled_since_view=max(count, 0),
        )

    def save(self, user_id: str, state: ScheduleState) -> None:
        if state.last_scheduled_at is None:
            return
        self._client.put_item(
            TableName=self._table_name,
            Item={
                **self._key(user_id),
                "last_scheduled_at": self._text(
                    state.last_scheduled_at.astimezone(UTC).isoformat()
                ),
                "scheduled_since_view": {"N": str(state.scheduled_since_view)},
            },
        )


class InMemoryScheduleStateStore:
    """Schedule pointers for the local dispatcher and tests."""

    def __init__(self) -> None:
        self._states: dict[str, ScheduleState] = {}

    def get(self, user_id: str) -> ScheduleState | None:
        return self._states.get(user_id)

    def save(self, user_id: str, state: ScheduleState) -> None:
        self._states[user_id] = state


__all__ = [
    "DynamoScheduleStateStore",
    "InMemoryScheduleStateStore",
    "ScheduleState",
    "ScheduleStateStore",
    "is_due",
    "next_bucket",
]
