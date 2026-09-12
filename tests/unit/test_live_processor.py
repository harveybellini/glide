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


def _place(place_id: str, longitude: float, latitude: float) -> PlaceRef:
    return PlaceRef(
        id=place_id,
        label=place_id,
        longitude=longitude,
        latitude=latitude,
        provenance="amazon-location-places",
        confirmed=False,
        storage_policy_status="ephemeral",
    )


def test_near_duplicate_place_candidates_resolve_to_the_top_match() -> None:
    """Amazon returns one record per facility: a terminal and its arrivals hall
    are 147 m apart, a building and the gallery inside it are 0 m apart. Those
    are one place, and the planner must not raise a decision for them."""

    from glide.live.processor import choose_place_match

    # The Shard: two records for the same building.
    shard = [
        _place("itsu", -0.0866, 51.5045),
        _place("view", -0.0866, 51.5045),
    ]
    assert choose_place_match(shard) is shard[0]

    # Heathrow Terminal 5: terminal, terminal, arrivals.
    heathrow = [
        _place("t5", -0.4905, 51.4724),
        _place("t5b", -0.4895, 51.4724),
        _place("arrivals", -0.4912, 51.4718),
    ]
    assert choose_place_match(heathrow) is heathrow[0]


def test_genuinely_ambiguous_place_candidates_stay_a_decision() -> None:
    """Three Costa branches in the City are ~390 m apart; a hotel that shares
    the airport's name is 6 km away. Neither is a single place."""

    from glide.live.processor import choose_place_match

    costa = [
        _place("costa-a", -0.0885, 51.5155),
        _place("costa-b", -0.0840, 51.5155),
        _place("costa-c", -0.0885, 51.5120),
    ]
    assert choose_place_match(costa) is None

    hotel = [
        _place("premier-inn", -0.4430, 51.4700),
        _place("airport", -0.4545, 51.4700),
    ]
    assert choose_place_match(hotel) is None


def test_candidates_without_coordinates_are_not_merged() -> None:
    from glide.live.processor import choose_place_match

    missing = PlaceRef(
        id="no-coords",
        label="no-coords",
        provenance="amazon-location-places",
        confirmed=False,
        storage_policy_status="ephemeral",
    )
    assert choose_place_match([_place("one", -0.1, 51.5), missing]) is None
    assert choose_place_match([]) is None
    assert choose_place_match([_place("only", -0.1, 51.5)]).id == "only"


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


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_decision_opened(self, *, settings, decision) -> None:
        del settings
        self.sent.append(decision.id)


def test_live_processor_announces_a_new_decision_once() -> None:
    settings, place_search, adapter = _fixtures()
    settings = settings.model_copy(
        update={"notification_email": "owner@example.com"}
    )
    store = DynamoDbStateStore(FakeDynamoDb(), "glide")
    store.save_settings(settings)
    notifier = RecordingNotifier()
    processor = build_live_processor(
        state_store=store,
        calendar_factory=lambda _settings: adapter,
        router_factory=lambda _settings: FixtureRouter(),
        place_search=place_search,
        notifier=notifier,
        clock=lambda: datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    processor.process(
        Job(id="job-1", user_id=settings.user_id, trigger="schedule", run_id="run-1")
    )
    first = store.get_decisions(settings.user_id)

    assert len(notifier.sent) == 1
    assert first and first[0].notified_at is not None

    # The second poll rebuilds the same open decision from the calendar. The
    # persisted mark must survive that rewrite and keep the user undisturbed.
    processor.process(
        Job(id="job-2", user_id=settings.user_id, trigger="schedule", run_id="run-2")
    )

    assert len(notifier.sent) == 1


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
    assert adapter.blocks == {}


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
