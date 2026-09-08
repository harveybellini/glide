from __future__ import annotations

from datetime import UTC, datetime

import pytest
from glide.adapters.dynamodb import DynamoDbStateStore
from glide.adapters.fixtures import (
    PLACES,
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
)
from glide.domain.live import StaleSourceError
from glide.domain.models import PlaceRef, RunStatus, UserSettings
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


def test_live_processor_requires_persisted_settings() -> None:
    settings, place_search, adapter = _fixtures()
    processor = build_live_processor(
        state_store=DynamoDbStateStore(FakeDynamoDb(), "glide"),
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
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


def test_stale_source_requeues_and_reconciles_on_retry() -> None:
    settings, place_search, adapter = _fixtures()
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
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
