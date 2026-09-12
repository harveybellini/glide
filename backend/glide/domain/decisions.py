"""Decision lifecycle policy shared by the sample and live processors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from glide.adapters.interfaces import StateStore
from glide.domain.models import (
    CalendarEvent,
    DecisionStatus,
    JourneyPlan,
    PlanAction,
    RunStatus,
    WorkflowResult,
)

# The one resolution that adds travel the arithmetic said did not fit. It is
# never proposed by the model: a resolved human decision sets it, and the
# executor applies it after validation, so the planner's feasibility rule is
# unchanged.
ADD_ANYWAY = "add_anyway"
USER_OVERRIDE_REASON = "user_override"

# Which resolutions make sense for each reason the planner can raise. A location
# correction is only offered where a location is actually the problem: a
# shortfall is arithmetic about time, and asking the user to "correct" a place
# that resolved cleanly told them to fix something that was not broken.
DECISION_ACTIONS: dict[str, tuple[str, ...]] = {
    "insufficient_time": (ADD_ANYWAY, "skip_journey", "edit_source_event"),
    "unknown_location": ("correct_location", "skip_journey", "edit_source_event"),
    "unknown_start": ("correct_location", "skip_journey", "edit_source_event"),
    "manual_edit": ("keep_manual_edit", "replace_with_plan", "skip_journey"),
    "manually_deleted": ("recreate_journey", "skip_journey"),
    "hybrid_meeting": ("treat_as_virtual", "treat_as_physical", "skip_journey"),
    "all_day": ("skip_journey", "edit_source_event"),
    "downstream_uncertain": ("skip_journey", "edit_source_event"),
}


def apply_forced_journeys(
    plans: Sequence[JourneyPlan],
    *,
    forced_journeys: set[str],
    events: Sequence[CalendarEvent],
    now: datetime,
) -> list[JourneyPlan]:
    """Turn an accepted "Add it anyway" into a create plan.

    The placement is the honest reading of the answer: the block ends when the
    destination appointment starts, so the user arrives on time and gives up
    the arrival buffer the shortfall quoted (recorded as a zero-minute buffer,
    which is what the block now holds). Nothing is invented - the drive
    duration comes from the decision's own facts - and the accepted shortfall
    is copied into the plan so the run keeps a record of what was overridden.

    A journey whose destination has already started is left as a decision:
    there is no longer anything to add.
    """

    if not forced_journeys:
        return list(plans)

    destination_starts = {event.occurrence_id: event.start for event in events}
    forced: list[JourneyPlan] = []
    for plan in plans:
        if (
            plan.journey_key not in forced_journeys
            or plan.action != PlanAction.DECISION
            or plan.reason_code != "insufficient_time"
        ):
            forced.append(plan)
            continue

        destination_start = destination_starts.get(plan.destination_occurrence_id)
        duration_seconds = plan.calculated_facts.get("duration_seconds")
        if (
            destination_start is None
            or not isinstance(duration_seconds, int)
            or isinstance(duration_seconds, bool)
            or duration_seconds <= 0
            or destination_start <= now
        ):
            forced.append(plan)
            continue

        forced.append(
            plan.model_copy(
                update={
                    "action": PlanAction.CREATE,
                    "reason_code": USER_OVERRIDE_REASON,
                    "padding_minutes": 0,
                    "proposed_start": destination_start
                    - timedelta(seconds=duration_seconds),
                    "proposed_end": destination_start,
                    "calculated_facts": {
                        **plan.calculated_facts,
                        "accepted_shortfall_seconds": plan.calculated_facts.get(
                            "shortfall_seconds", 0
                        ),
                    },
                }
            )
        )
    return forced


def close_stale_decisions(state_store: StateStore, result: WorkflowResult) -> None:
    """Close open decisions that this completed run did not reproduce.

    Decision ids are keyed by occurrence plus source revision, so an identical
    poll recreates the same id and stays open, while a resolved conflict or a
    changed source supersedes the older open record instead of stacking
    duplicate alerts.
    """

    if result.run.status not in (RunStatus.COMPLETED, RunStatus.NEEDS_INPUT):
        return
    current_ids = {decision.id for decision in result.decisions}
    updates = [
        decision.model_copy(update={"status": DecisionStatus.STALE})
        for decision in state_store.get_decisions(result.run.user_id)
        if decision.status == DecisionStatus.OPEN and decision.id not in current_ids
    ]
    if updates:
        state_store.save_decisions(updates)
