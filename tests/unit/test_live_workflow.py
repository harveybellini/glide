from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    local_datetime,
    place_index,
)
from glide.adapters.google_calendar import (
    CalendarConflictError,
    CalendarNotFoundError,
)
from glide.domain.live import LiveWorkflow, StaleSourceError
from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    ManagedBlock,
    MutationOperation,
    PlanAction,
    RunStatus,
    Transparency,
    UserSettings,
)

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)


class FakeCalendarAdapter:
    def __init__(
        self,
        blocks: list[ManagedBlock] | None = None,
        source_events: list[CalendarEvent] | None = None,
    ) -> None:
        self.blocks: dict[str, ManagedBlock] = {}
        self.source_events = source_events or []
        self.source_changed_on_call: int | None = None
        self.list_calls = 0
        self._etag_counter = 1
        self.conflict_event_ids: set[str] = set()
        for block in blocks or []:
            self.blocks[block.provider_event_id] = block

    def list_events(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarEvent]:
        self.list_calls += 1
        events = self.source_events
        if self.source_changed_on_call == self.list_calls and events:
            first = events[0]
            events = [first.model_copy(update={"etag": "changed-by-user"}), *events[1:]]
        return events

    def list_blocks(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ManagedBlock]:
        return sorted(
            (
                block
                for block in self.blocks.values()
                if block.start < window_end and block.end > window_start
            ),
            key=lambda block: block.start,
        )

    def ensure_travel_calendar(self, calendar_id: str) -> str:
        return "glide-travel"

    def _next_etag(self) -> str:
        etag = f"etag-{self._etag_counter}"
        self._etag_counter += 1
        return etag

    def create_block(self, *, calendar_id: str, block: ManagedBlock) -> ManagedBlock:
        event_id = f"travel-{len(self.blocks) + 1}"
        stored = block.model_copy(
            update={"provider_event_id": event_id, "etag": self._next_etag()}
        )
        self.blocks[event_id] = stored
        return stored

    def update_block(
        self,
        *,
        calendar_id: str,
        block: ManagedBlock,
        expected_etag: str | None = None,
    ) -> ManagedBlock:
        current = self.blocks.get(block.provider_event_id)
        if current is None:
            raise CalendarNotFoundError(block.provider_event_id)
        if (
            block.provider_event_id in self.conflict_event_ids
            or (expected_etag is not None and current.etag != expected_etag)
        ):
            raise CalendarConflictError("travel block changed since it was read")
        stored = block.model_copy(update={"etag": self._next_etag()})
        self.blocks[block.provider_event_id] = stored
        return stored

    def delete_block(
        self,
        *,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> None:
        current = self.blocks.get(event_id)
        if current is None:
            raise CalendarNotFoundError(event_id)
        if event_id in self.conflict_event_ids or (
            expected_etag is not None and current.etag != expected_etag
        ):
            raise CalendarConflictError("travel block changed since it was read")
        del self.blocks[event_id]


def _workflow(adapter: FakeCalendarAdapter) -> LiveWorkflow:
    return LiveWorkflow(
        settings=UserSettings.model_validate(canonical_settings()),
        calendar=adapter,
        glide_calendar_id="glide-travel",
        router=FixtureRouter(),
    )


def _source(calendar: FixtureCalendar):
    events = calendar.events()
    return events, place_index(events)


def _run(workflow: LiveWorkflow, events, index, trigger: str = "live"):
    workflow.calendar.source_events = events
    return workflow.run(
        trigger=trigger,
        source_events=events,
        place_index=index,
        now=NOW,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )


def test_first_run_creates_block_and_raises_conflict_decision() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert result.run.status == RunStatus.NEEDS_INPUT
    assert len(result.travel_blocks) == 1
    assert len(result.decisions) == 1
    assert result.decisions[0].reason == "insufficient_time"
    creates = [
        receipt
        for receipt in result.receipts
        if receipt.operation == MutationOperation.CREATE
    ]
    assert len(creates) == 1
    assert creates[0].provider_event_id.startswith("travel-")
    assert result.travel_blocks[0].provider_event_id == creates[0].provider_event_id


def test_scheduled_run_keeps_its_trigger_in_the_result() -> None:
    """Regression: scheduled maintenance used to be persisted as ``live``.

    The run row is written by the already-accepted route and then overwritten
    by the workflow result, so a hard-coded trigger silently relabelled every
    background run and made scheduled runs impossible to audit.
    """

    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    result = _run(workflow, events, index, trigger="schedule")

    assert result.run.trigger == "schedule"


def test_noop_rerun_produces_no_duplicate_blocks() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    first = _run(workflow, events, index)
    second = _run(workflow, events, index)

    assert len(second.travel_blocks) == 1
    assert len(adapter.blocks) == 1
    assert all(
        receipt.operation != MutationOperation.CREATE for receipt in second.receipts
    )
    assert sum(
        receipt.operation == MutationOperation.NOOP for receipt in second.receipts
    ) == 1
    assert first.decisions[0].id == second.decisions[0].id


def test_moved_source_updates_first_block_and_creates_second() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    _run(workflow, events, index)

    calendar.move("occ_b", local_datetime(DAY, 10, 45), local_datetime(DAY, 11, 15))
    events, index = _source(calendar)
    result = _run(workflow, events, index)

    assert result.run.status == RunStatus.COMPLETED
    assert len(result.travel_blocks) == 2
    assert result.decisions == ()
    operations = [receipt.operation for receipt in result.receipts]
    assert MutationOperation.UPDATE in operations
    assert MutationOperation.CREATE in operations
    assert len(adapter.blocks) == 2


def test_deleted_source_removes_orphans_and_recalculates_direct_journey() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    _run(workflow, events, index)
    calendar.move("occ_b", local_datetime(DAY, 10, 45), local_datetime(DAY, 11, 15))
    events, index = _source(calendar)
    _run(workflow, events, index)
    assert len(adapter.blocks) == 2

    calendar.delete("occ_b")
    events, index = _source(calendar)
    result = _run(workflow, events, index)

    assert len(result.travel_blocks) == 1
    assert len(adapter.blocks) == 1
    removes = [
        receipt
        for receipt in result.receipts
        if receipt.operation == MutationOperation.REMOVE
    ]
    assert len(removes) == 1
    assert sum(
        receipt.operation == MutationOperation.UPDATE for receipt in result.receipts
    ) == 1
    create = next(
        plan for plan in result.plans if plan.action == PlanAction.CREATE
    )
    assert create.destination_occurrence_id == "occ_c"


def test_manual_edit_keeps_block_and_suspends_management() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    first = _run(workflow, events, index)
    block = first.travel_blocks[0]

    # The user shortens the block directly in Google Calendar.
    adapter.blocks[block.provider_event_id] = block.model_copy(
        update={"end": block.end - timedelta(minutes=15)}
    )

    result = _run(workflow, events, index)

    assert result.decisions[0].reason == "manual_edit"
    assert result.travel_blocks[0].end == block.end - timedelta(minutes=15)
    assert all(
        receipt.operation
        not in (MutationOperation.CREATE, MutationOperation.UPDATE)
        for receipt in result.receipts
    )


def test_manually_edited_orphan_block_is_kept_with_decision() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    first = _run(workflow, events, index)
    block = first.travel_blocks[0]

    # The user edits the block, then the source appointment disappears so the
    # journey no longer exists at all.
    adapter.blocks[block.provider_event_id] = block.model_copy(
        update={"end": block.end - timedelta(minutes=15)}
    )
    calendar.delete("occ_b")
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert any(decision.reason == "manual_edit" for decision in result.decisions)
    assert block.provider_event_id in adapter.blocks
    assert all(
        receipt.operation != MutationOperation.REMOVE
        for receipt in result.receipts
    )


def test_manually_edited_block_survives_a_remove_plan() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    first = _run(workflow, events, index)
    block = first.travel_blocks[0]

    # The user edits the block, then the appointment moves onto the previous
    # venue so the deterministic plan becomes a same-place removal.
    adapter.blocks[block.provider_event_id] = block.model_copy(
        update={"end": block.end - timedelta(minutes=15)}
    )
    calendar.move(
        "occ_b",
        local_datetime(DAY, 11, 0),
        local_datetime(DAY, 11, 30),
        new_location="Northside Community Centre",
    )
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert any(decision.reason == "manual_edit" for decision in result.decisions)
    assert block.provider_event_id in adapter.blocks
    assert all(
        receipt.operation != MutationOperation.REMOVE
        for receipt in result.receipts
    )


def test_create_plan_requires_proposed_times() -> None:
    from glide.domain.models import JourneyPlan

    workflow = _workflow(FakeCalendarAdapter())
    plan = JourneyPlan(
        journey_key="key",
        origin_occurrence_id="start_place",
        destination_occurrence_id="occ_b",
        source_calendar_id="fixture-primary",
        source_etags={},
        route_estimate_id="estimate",
        proposed_start=None,
        proposed_end=None,
        padding_minutes=10,
        action=PlanAction.CREATE,
        reason_code="feasible",
    )

    with pytest.raises(ValueError, match="proposed start and end"):
        workflow._apply_create(
            plan=plan,
            existing=None,
            previous=None,
            fingerprint="fingerprint",
            now=NOW,
            run_id="run-1",
            blocks={},
            receipts=[],
            decisions=[],
        )


def test_detect_manual_override_skips_blocks_without_hash() -> None:
    block = ManagedBlock(
        journey_key="key",
        user_id="sample-user",
        provider_event_id="travel-1",
        start=NOW + timedelta(hours=1),
        end=NOW + timedelta(hours=1, minutes=35),
        last_applied_hash="",
        etag="etag",
        source_revision="revision",
        policy_revision=1,
    )
    workflow = _workflow(FakeCalendarAdapter())

    result = workflow._detect_manual_override(block)

    assert result is block
    assert result.manual_override is False


def test_manual_deletion_is_never_immediately_recreated() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    first = _run(workflow, events, index)
    block = first.travel_blocks[0]

    # The user deleted the managed event; the state store still remembers it.
    del adapter.blocks[block.provider_event_id]
    result = workflow.run(
        source_events=events,
        place_index=index,
        now=NOW,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        previous_blocks=[block],
    )

    assert result.decisions[0].reason == "manually_deleted"
    assert result.travel_blocks == ()
    assert len(adapter.blocks) == 0

    later = workflow.run(
        source_events=events,
        place_index=index,
        now=NOW,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        previous_blocks=[block],
        manual_deletions={block.journey_key},
    )

    assert any(decision.reason == "manually_deleted" for decision in later.decisions)
    assert later.travel_blocks == ()
    assert len(adapter.blocks) == 0


def test_etag_conflict_fails_safely_for_retry() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    first = _run(workflow, events, index)
    adapter.conflict_event_ids.add(first.travel_blocks[0].provider_event_id)
    calendar.move("occ_b", local_datetime(DAY, 10, 45), local_datetime(DAY, 11, 15))
    events, index = _source(calendar)

    with pytest.raises(CalendarConflictError):
        _run(workflow, events, index)


def test_past_orphan_block_is_preserved() -> None:
    ghost = ManagedBlock(
        journey_key="ghost-key",
        user_id="sample-user",
        provider_event_id="travel-ghost",
        start=NOW - timedelta(hours=1),
        end=NOW - timedelta(minutes=30),
        last_applied_hash="ghost-hash",
        etag="etag-ghost",
        source_revision="old",
        policy_revision=1,
    )
    adapter = FakeCalendarAdapter(blocks=[ghost])
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert "ghost-key" in {block.journey_key for block in result.travel_blocks}
    assert len(adapter.blocks) == 2


def test_foreign_owned_block_is_never_mutated() -> None:
    foreign = ManagedBlock(
        journey_key="foreign-key",
        user_id="another-user",
        provider_event_id="travel-foreign",
        start=NOW + timedelta(hours=4),
        end=NOW + timedelta(hours=5),
        last_applied_hash="foreign-hash",
        etag="etag-foreign",
        source_revision="old",
        policy_revision=1,
    )
    adapter = FakeCalendarAdapter(blocks=[foreign])
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert "travel-foreign" in adapter.blocks
    assert "foreign-key" not in {block.journey_key for block in result.travel_blocks}
    assert all(
        receipt.provider_event_id != "travel-foreign" for receipt in result.receipts
    )


def test_paused_run_makes_no_writes() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    workflow.settings = workflow.settings.model_copy(update={"enabled": False})
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    result = _run(workflow, events, index)

    assert result.run.status == RunStatus.PAUSED
    assert result.receipts == ()
    assert result.travel_blocks == ()
    assert len(adapter.blocks) == 0


def test_hybrid_meeting_surfaces_decision_without_writing_a_block() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    hybrid = CalendarEvent(
        provider_event_id="evt_h",
        occurrence_id="occ_h",
        calendar_id="fixture-primary",
        etag="etag-h",
        start=datetime(2026, 9, 9, 13, 0, tzinfo=UTC),
        end=datetime(2026, 9, 9, 14, 0, tzinfo=UTC),
        original_time_zone="Europe/London",
        title="Design review",
        location="Hybrid Venue",
        status=EventStatus.CONFIRMED,
        transparency=Transparency.OPAQUE,
        attendance=Attendance.ACCEPTED,
        kind=EventKind.UNKNOWN,
    )

    result = _run(workflow, [*events, hybrid], index)

    hybrid_decisions = [
        decision
        for decision in result.decisions
        if decision.reason == "hybrid_meeting"
    ]
    assert len(hybrid_decisions) == 1
    assert hybrid_decisions[0].occurrence_id == "occ_h"
    assert "treat_as_virtual" in hybrid_decisions[0].allowed_actions
    assert all(
        block.journey_key != hybrid_decisions[0].journey_key
        for block in result.travel_blocks
    )


def test_source_change_after_read_requeues_without_writes() -> None:
    adapter = FakeCalendarAdapter()
    workflow = _workflow(adapter)
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)
    adapter.source_events = events
    adapter.source_changed_on_call = 1

    with pytest.raises(StaleSourceError):
        _run(workflow, events, index)

    assert len(adapter.blocks) == 0


def test_crash_after_write_reconciles_without_duplicates() -> None:
    adapter = FakeCalendarAdapter()
    calendar = FixtureCalendar(day=DAY)
    events, index = _source(calendar)

    # The first worker wrote the block but "crashed" before persisting a
    # result; a fresh worker sees only the provider's block.
    _run(_workflow(adapter), events, index)
    assert len(adapter.blocks) == 1

    second = _run(_workflow(adapter), events, index)

    assert len(adapter.blocks) == 1
    assert all(
        receipt.operation != MutationOperation.CREATE for receipt in second.receipts
    )
    assert sum(
        receipt.operation == MutationOperation.NOOP for receipt in second.receipts
    ) == 1
