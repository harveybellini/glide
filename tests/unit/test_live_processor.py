from __future__ import annotations

from datetime import UTC, datetime

import pytest
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.adapters.fixtures import (
    PLACES,
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    local_datetime,
)
from glide.domain.live import SettingsChangedError, StaleSourceError
from glide.domain.models import (
    DecisionStatus,
    PlaceRef,
    RunStatus,
    UserSettings,
)
from glide.jobs.queue import Job
from glide.live.processor import build_live_processor

from tests.unit.test_dynamodb import FakeDynamoDb
from tests.unit.test_live_workflow import FakeCalendarAdapter

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()


class FakeLiveCalendar(FakeCalendarAdapter):
    def __init__(self, source_events) -> None:
        super().__init__()
        self.source_events = source_events

    def list_events(self, *, calendar_id, window_start, window_end):
        events = super().list_events(
            calendar_id=calendar_id,
            window_start=window_start,
            window_end=window_end,
        )
        return [
            event
            for event in events
            if event.start < window_end and event.end > window_start
        ]


class FakePlaceSearch:
    def __init__(self, mapping: dict[str, PlaceRef]) -> None:
        self.mapping = mapping

    def search(self, *, query, region=None, storage_allowed=False):
        place = self.mapping.get(query)
        return [place] if place is not None else []


def _fixtures():
    calendar = FixtureCalendar(day=DAY)
    settings = UserSettings.model_validate(
        canonical_settings(user_id="google:subject")
    )
    place_search = FakePlaceSearch(
        {
            "Northside Community Centre": PLACES["a"],
            "Westfield Surgery": PLACES["b"],
            "Oakfield Primary School": PLACES["c"],
        }
    )
    adapter = FakeLiveCalendar(calendar.events())
    return settings, place_search, adapter


def test_live_processor_reads_plans_reconciles_and_persists() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    processor.process(
        Job(id="job-1", user_id=settings.user_id, trigger="schedule", run_id="run-1")
    )

    run = store.get_run("run-1")
    assert run is not None
    assert run.status == RunStatus.NEEDS_INPUT
    assert len(store.get_blocks(settings.user_id)) == 1
    assert len(store.get_decisions(settings.user_id)) == 1
    assert store.get_receipts("run-1")


def test_live_processor_rerun_is_idempotent() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    processor.process(
        Job(id="job-1", user_id=settings.user_id, trigger="schedule", run_id="run-1")
    )
    processor.process(
        Job(id="job-2", user_id=settings.user_id, trigger="schedule", run_id="run-2")
    )

    assert len(store.get_blocks(settings.user_id)) == 1
    second = store.get_receipts("run-2")
    assert second and all(receipt.outcome.value == "unchanged" for receipt in second)
    assert store.get_run("run-2").status == RunStatus.NEEDS_INPUT


def test_live_processor_persists_manual_deletion_across_runs() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    processor.process(
        Job(id="job-1", user_id=settings.user_id, trigger="schedule", run_id="run-1")
    )
    created = next(iter(adapter.blocks))
    del adapter.blocks[created]

    processor.process(
        Job(id="job-2", user_id=settings.user_id, trigger="schedule", run_id="run-2")
    )
    assert any(
        decision.reason == "manually_deleted"
        for decision in store.get_decisions(settings.user_id)
    )
    assert adapter.blocks == {}

    processor.process(
        Job(id="job-3", user_id=settings.user_id, trigger="schedule", run_id="run-3")
    )
    assert adapter.blocks == {}


def test_resolved_skip_is_durable_until_the_source_revision_changes() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    processor.process(
        Job(id="job-1", user_id=settings.user_id, trigger="schedule", run_id="run-1")
    )
    open_decision = next(
        decision
        for decision in store.get_decisions(settings.user_id)
        if decision.status == DecisionStatus.OPEN
    )
    store.save_decisions(
        [
            open_decision.model_copy(
                update={
                    "status": DecisionStatus.RESOLVED,
                    "resolution": "skip_journey",
                }
            )
        ]
    )
    blocks_after_first_run = len(store.get_blocks(settings.user_id))

    processor.process(
        Job(id="job-2", user_id=settings.user_id, trigger="schedule", run_id="run-2")
    )
    assert store.get_run("run-2").status == RunStatus.COMPLETED
    assert len(store.get_blocks(settings.user_id)) == blocks_after_first_run
    assert not any(
        decision.status == DecisionStatus.OPEN
        and decision.journey_key == open_decision.journey_key
        for decision in store.get_decisions(settings.user_id)
    )

    # A source edit produces a new revision, so the journey is reconsidered.
    calendar = FixtureCalendar(day=DAY)
    calendar.move("occ_a", local_datetime(DAY, 9, 15), local_datetime(DAY, 10, 0))
    adapter.source_events = calendar.events()
    processor.process(
        Job(id="job-3", user_id=settings.user_id, trigger="schedule", run_id="run-3")
    )
    assert any(
        decision.status == DecisionStatus.OPEN
        and decision.journey_key == open_decision.journey_key
        for decision in store.get_decisions(settings.user_id)
    )


def test_live_processor_requires_persisted_settings() -> None:
    settings, place_search, adapter = _fixtures()
    processor = build_live_processor(
        state_store=DynamoDbStateStore(FakeDynamoDb(), "glide"),
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="no persisted settings"):
        processor.process(
            Job(
                id="job-1",
                user_id=settings.user_id,
                trigger="schedule",
                run_id="run-1",
            )
        )


def test_settings_revision_change_discards_a_stale_result() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    adapter.create_block_original = adapter.create_block

    def bump_then_create(*, calendar_id, block):
        current = store.get_settings(settings.user_id)
        store.save_settings(
            current.model_copy(update={"revision": current.revision + 1})
        )
        return adapter.create_block_original(calendar_id=calendar_id, block=block)

    adapter.create_block = bump_then_create  # type: ignore[method-assign]
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    with pytest.raises(SettingsChangedError):
        processor.process(
            Job(
                id="job-1",
                user_id=settings.user_id,
                trigger="schedule",
                run_id="run-1",
            )
        )
    assert store.get_blocks(settings.user_id) == []


def test_stale_source_requeues_and_reconciles_on_retry() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    # The source changes between the initial read and the pre-write re-read.
    adapter.source_changed_on_call = 2
    with pytest.raises(StaleSourceError):
        processor.process(
            Job(
                id="job-1",
                user_id=settings.user_id,
                trigger="schedule",
                run_id="run-1",
            )
        )
    assert store.get_blocks(settings.user_id) == []

    # The retry sees a stable source and reconciles normally.
    adapter.source_changed_on_call = None
    processor.process(
        Job(
            id="job-2",
            user_id=settings.user_id,
            trigger="schedule",
            run_id="run-2",
        )
    )
    assert len(store.get_blocks(settings.user_id)) == 1
