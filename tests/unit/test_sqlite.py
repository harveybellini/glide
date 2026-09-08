from __future__ import annotations

from datetime import date, datetime

from glide.adapters.fixtures import FixtureCalendar, FixtureRouter, local_datetime, place_index
from glide.adapters.sqlite import SqliteStateStore
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import UserSettings


def _run_sample() -> object:
    day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 6, 0, tzinfo=local_datetime(day, 6).tzinfo)
    settings = UserSettings.model_validate(
        {
            "user_id": "user-1",
            "time_zone": "Europe/London",
            "source_calendar_id": "fixture-primary",
            "glide_calendar_id": "fixture-glide-travel",
            "start_place": {
                "id": "place_a",
                "provider_id": "place_a",
                "label": "Northside Community Centre",
                "provenance": "synthetic fixture",
                "confirmed": True,
                "storage_policy_status": "ephemeral",
            },
            "earliest_departure": "06:00:00",
            "mode": "driving",
            "padding_minutes": 10,
            "enabled": True,
            "revision": 1,
        }
    )
    calendar = FixtureCalendar(day=day)
    workflow = SampleWorkflow(
        settings=settings,
        travel_calendar=InMemoryTravelCalendar(),
        router=FixtureRouter(),
    )
    return workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )


def test_sqlite_state_store_round_trip(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    result = _run_sample()

    store.save_result(result)

    assert store.get_run(result.run.id) == result.run
    assert sorted(store.get_plans(result.run.id), key=lambda plan: plan.journey_key) == sorted(
        result.plans, key=lambda plan: plan.journey_key
    )
    assert sorted(
        store.get_blocks(result.run.user_id), key=lambda block: block.journey_key
    ) == sorted(result.travel_blocks, key=lambda block: block.journey_key)
    assert store.get_decisions(result.run.user_id) == list(result.decisions)
    assert store.get_receipts(result.run.id) == list(result.receipts)
