"""Decision lifecycle policy tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from glide.domain.decisions import (
    USER_OVERRIDE_REASON,
    apply_forced_journeys,
    close_stale_decisions,
)
from glide.domain.models import (
    CalendarEvent,
    EventKind,
    JourneyPlan,
    PlanAction,
    Run,
    RunStatus,
    WorkflowResult,
)


def _event(*, occurrence_id: str, start: datetime) -> CalendarEvent:
    return CalendarEvent(
        provider_event_id=f"provider-{occurrence_id}",
        occurrence_id=occurrence_id,
        calendar_id="primary",
        etag="etag-1",
        start=start,
        end=start + timedelta(minutes=15),
        original_time_zone="Europe/London",
        title="Appointment",
        kind=EventKind.PHYSICAL,
    )


def _shortfall_plan() -> JourneyPlan:
    return JourneyPlan(
        journey_key="journey-1",
        origin_occurrence_id="occ-a",
        destination_occurrence_id="occ-b",
        source_calendar_id="primary",
        source_etags={"occ-a": "etag-a", "occ-b": "etag-b"},
        route_estimate_id="estimate-1",
        padding_minutes=10,
        action=PlanAction.DECISION,
        reason_code="insufficient_time",
        calculated_facts={
            "duration_seconds": 1800,
            "available_seconds": 1800,
            "required_seconds": 2400,
            "shortfall_seconds": 600,
        },
    )


def test_add_anyway_books_the_journey_ending_at_the_appointment() -> None:
    destination_start = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
    destination = _event(occurrence_id="occ-b", start=destination_start)

    plans = apply_forced_journeys(
        [_shortfall_plan()],
        forced_journeys={"journey-1"},
        events=[destination],
        now=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
    )

    plan = plans[0]
    assert plan.action == PlanAction.CREATE
    assert plan.reason_code == USER_OVERRIDE_REASON
    assert plan.proposed_end == destination_start
    assert plan.proposed_start == datetime(2026, 9, 12, 10, 30, tzinfo=UTC)
    assert plan.padding_minutes == 0
    assert plan.calculated_facts["accepted_shortfall_seconds"] == 600


def test_forced_journeys_leaves_other_journeys_as_decisions() -> None:
    destination = _event(
        occurrence_id="occ-b", start=datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
    )

    plans = apply_forced_journeys(
        [_shortfall_plan()],
        forced_journeys={"another-journey"},
        events=[destination],
        now=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
    )

    assert plans[0].action == PlanAction.DECISION
    assert plans[0].proposed_start is None


def test_add_anyway_does_not_book_an_appointment_that_already_started() -> None:
    destination = _event(
        occurrence_id="occ-b", start=datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
    )

    plans = apply_forced_journeys(
        [_shortfall_plan()],
        forced_journeys={"journey-1"},
        events=[destination],
        now=datetime(2026, 9, 12, 11, 5, tzinfo=UTC),
    )

    assert plans[0].action == PlanAction.DECISION


def test_close_stale_decisions_skips_nonterminal_runs() -> None:
    class Guard:
        def get_decisions(self, user_id):
            raise AssertionError("stale scan must not run for a queued run")

        def save_decisions(self, decisions):
            raise AssertionError("stale scan must not write for a queued run")

    result = WorkflowResult(
        run=Run(
            id="run-1",
            user_id="user-1",
            trigger="manual",
            status=RunStatus.QUEUED,
            lease_revision=1,
            source_fingerprint="",
            started_at=datetime(2026, 9, 9, tzinfo=UTC),
        ),
        plans=(),
        decisions=(),
        travel_blocks=(),
        receipts=(),
        source_events=(),
    )

    close_stale_decisions(Guard(), result)
