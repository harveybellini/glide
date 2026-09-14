"""Provider-facing adapter contracts.

The live Google and Amazon Location implementations will live in dedicated
modules owned by the integration workers. Keeping these protocols here lets
the domain engine, fixtures, and tests share one shape now.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from glide.domain.models import (
    CalendarEvent,
    Decision,
    JourneyPlan,
    ManagedBlock,
    MutationReceipt,
    PlaceRef,
    Run,
    SampleSnapshot,
    TravelMode,
    UserSettings,
    WorkflowResult,
)


class CalendarAdapter(Protocol):
    def list_events(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarEvent]: ...

    def list_blocks(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ManagedBlock]: ...

    def create_block(
        self,
        *,
        calendar_id: str,
        block: ManagedBlock,
    ) -> ManagedBlock: ...

    def update_block(
        self,
        *,
        calendar_id: str,
        block: ManagedBlock,
        expected_etag: str | None = None,
    ) -> ManagedBlock: ...

    def delete_block(
        self,
        *,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> None: ...


class PlaceLookup(Protocol):
    def search(
        self,
        *,
        query: str,
        region: str | None = None,
        storage_allowed: bool = False,
    ) -> list[PlaceRef]: ...


class RoutingProvider(Protocol):
    def estimate(
        self,
        *,
        origin_place_id: str,
        destination_place_id: str,
        mode: TravelMode,
        departure_at: datetime | None = None,
        arrival_by: datetime | None = None,
    ) -> object: ...


class DecisionNotifier(Protocol):
    """Sends one "Glide needs your decision" message to a user.

    Implementations own transport and delivery reporting only. Deciding
    *whether* a decision should be announced is domain policy and lives in
    ``glide.domain.notifications`` so that every transport shares the same
    once-only rule.
    """

    def send_decision_opened(
        self,
        *,
        settings: UserSettings,
        decision: Decision,
    ) -> None: ...


class StateStore(Protocol):
    """Durable state contract shared by the SQLite and DynamoDB adapters."""

    def save_settings(self, settings: UserSettings) -> None: ...

    def get_settings(self, user_id: str) -> UserSettings | None: ...

    def touch_last_viewed_at(self, user_id: str, viewed_at: datetime) -> None:
        """Record that the owner looked at their day.

        This is display bookkeeping, not settings, so it deliberately does not
        bump the settings revision: a view must never fence an in-flight run.
        Implementations may throttle or skip the write.
        """

        ...

    def save_run(self, run: Run) -> None: ...

    def get_run(self, run_id: str) -> Run | None: ...

    def save_plans(self, run_id: str, plans: list[JourneyPlan]) -> None: ...

    def get_plans(self, run_id: str) -> list[JourneyPlan]: ...

    def save_decisions(self, decisions: list[Decision]) -> None: ...

    def get_decisions(self, user_id: str) -> list[Decision]: ...

    def save_receipts(
        self,
        receipts: list[MutationReceipt],
        user_id: str = "",
    ) -> None: ...

    def get_receipts(self, run_id: str | None = None) -> list[MutationReceipt]: ...

    def save_blocks(self, user_id: str, blocks: list[ManagedBlock]) -> None: ...

    def get_blocks(self, user_id: str) -> list[ManagedBlock]: ...

    def save_sample_snapshot(self, snapshot: SampleSnapshot) -> None: ...

    def get_sample_snapshot(self, user_id: str) -> SampleSnapshot | None: ...

    def get_sample_snapshot_by_session(
        self,
        session_id: str,
    ) -> SampleSnapshot | None: ...

    def get_latest_run(self, user_id: str) -> Run | None: ...

    def get_user_receipts(self, user_id: str) -> list[MutationReceipt]: ...

    def save_result(self, result: WorkflowResult) -> None: ...

    def clear_user(self, user_id: str) -> None: ...

    def close(self) -> None: ...
