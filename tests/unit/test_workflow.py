from __future__ import annotations

from datetime import date, datetime

from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    local_datetime,
    place_index,
)
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import PlanAction, RunStatus, UserSettings


def _workflow(day: date) -> tuple[FixtureCalendar, FixtureRouter, SampleWorkflow]:
    calendar = FixtureCalendar(day=day)
    router = FixtureRouter()
    workflow = SampleWorkflow(
        settings=UserSettings.model_validate(
            {
                "user_id": "sample-user",
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
        ),
        travel_calendar=InMemoryTravelCalendar(),
        router=router,
    )
    return calendar, router, workflow


def test_canonical_sample_day_flow() -> None:
    day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 6, 0, tzinfo=local_datetime(day, 6).tzinfo)
    calendar, router, workflow = _workflow(day)

    first = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    assert len(first.travel_blocks) == 1
    assert first.decisions[0].calculated_facts["shortfall_seconds"] == 600

    calendar.move("occ_b", local_datetime(day, 10, 45), local_datetime(day, 11, 15))
    second = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    assert len(second.travel_blocks) == 2
    assert not second.decisions

    repeat = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    assert len(repeat.travel_blocks) == 2
    assert all(receipt.outcome.value == "unchanged" for receipt in repeat.receipts)

    calendar.delete("occ_b")
    deleted = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    assert len(deleted.travel_blocks) == 1
    create = next(plan for plan in deleted.plans if plan.action == PlanAction.CREATE)
    assert create.destination_occurrence_id == "occ_c"
    assert router.calls


def test_paused_workflow_makes_no_changes() -> None:
    day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 6, 0, tzinfo=local_datetime(day, 6).tzinfo)
    calendar, router, workflow = _workflow(day)
    workflow.settings = workflow.settings.model_copy(update={"enabled": False})

    result = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )

    assert result.run.status == RunStatus.PAUSED
    assert result.travel_blocks == ()
    assert result.receipts == ()


def test_run_reports_needs_input_when_a_decision_remains() -> None:
    day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 6, 0, tzinfo=local_datetime(day, 6).tzinfo)
    calendar, router, workflow = _workflow(day)

    result = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )

    assert result.decisions
    assert result.run.status == RunStatus.NEEDS_INPUT


def test_manual_override_suspends_updates_and_creates_decision() -> None:
    day = date(2026, 9, 9)
    now = datetime(2026, 9, 9, 6, 0, tzinfo=local_datetime(day, 6).tzinfo)
    calendar, router, workflow = _workflow(day)

    first = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    block = first.travel_blocks[0]
    manual_start = local_datetime(day, 10, 30).astimezone(block.start.tzinfo)
    workflow.travel_calendar.mark_manual_override(
        block.journey_key,
        manual_start,
        local_datetime(day, 10, 55).astimezone(block.end.tzinfo),
    )

    calendar.move("occ_b", local_datetime(day, 10, 45), local_datetime(day, 11, 15))
    second = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )

    assert len(second.travel_blocks) == 2
    assert block.journey_key in {b.journey_key for b in second.travel_blocks}
    kept = next(b for b in second.travel_blocks if b.journey_key == block.journey_key)
    assert kept.start == manual_start
    assert any(decision.reason == "manual_edit" for decision in second.decisions)
