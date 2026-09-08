"""Run the canonical Glide sample day from the command line."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    local_datetime,
    place_index,
)
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import PlanAction, UserSettings


def print_result(label: str, result: object, time_zone: ZoneInfo) -> None:
    print(f"\n=== {label} ===")
    workflow = result
    print(
        "  blocks: "
        + ", ".join(
            f"{b.start.astimezone(time_zone):%H:%M}->{b.end.astimezone(time_zone):%H:%M} "
            f"{b.journey_key[:8]}"
            for b in workflow.travel_blocks
        )
    )
    for decision in workflow.decisions:
        print(
            f"  decision: {decision.reason} "
            f"available={decision.calculated_facts.get('available_seconds', 0)//60}m "
            f"required={decision.calculated_facts.get('required_seconds', 0)//60}m "
            f"shortfall={decision.calculated_facts.get('shortfall_seconds', 0)//60}m"
        )
    print(
        "  receipts: "
        + ", ".join(f"{r.operation.value}:{r.outcome.value}" for r in workflow.receipts)
    )


def main() -> None:
    time_zone = ZoneInfo("Europe/London")
    day = date.today() + timedelta(days=1)
    now = datetime.now(time_zone)
    settings = UserSettings.model_validate(canonical_settings())
    calendar = FixtureCalendar(day=day)
    router = FixtureRouter()
    travel_calendar = InMemoryTravelCalendar()
    workflow = SampleWorkflow(
        settings=settings,
        travel_calendar=travel_calendar,
        router=router,
    )

    result_1 = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    print_result("First check: feasible A->B, conflict B->C", result_1, time_zone)
    assert len(result_1.travel_blocks) == 1
    assert len(result_1.decisions) == 1

    calendar.move(
        "occ_b",
        local_datetime(day, 10, 45),
        local_datetime(day, 11, 15),
    )
    result_2 = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    print_result("Move appointment and recheck", result_2, time_zone)
    assert len(result_2.travel_blocks) == 2
    assert len(result_2.decisions) == 0

    result_3 = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    print_result("Repeat check: idempotent", result_3, time_zone)
    assert len(result_3.travel_blocks) == 2
    assert all(receipt.outcome.value == "unchanged" for receipt in result_3.receipts)

    calendar.delete("occ_b")
    result_4 = workflow.run(
        source_events=calendar.events(),
        place_index=place_index(calendar.events()),
        now=now,
    )
    print_result("Delete appointment and reconcile direct A->C", result_4, time_zone)
    assert len(result_4.travel_blocks) == 1
    assert len(result_4.decisions) == 0

    create_plan = next(plan for plan in result_4.plans if plan.action == PlanAction.CREATE)
    assert create_plan.destination_occurrence_id == "occ_c"
    print("\nSample workflow completed successfully.")


if __name__ == "__main__":
    main()
