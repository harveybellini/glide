"""Decision lifecycle policy shared by the sample and live processors."""

from __future__ import annotations

from glide.adapters.interfaces import StateStore
from glide.domain.models import DecisionStatus, RunStatus, WorkflowResult

# Which resolutions make sense for each reason the planner can raise. A location
# correction is only offered where a location is actually the problem: a
# shortfall is arithmetic about time, and asking the user to "correct" a place
# that resolved cleanly told them to fix something that was not broken.
DECISION_ACTIONS: dict[str, tuple[str, ...]] = {
    "insufficient_time": ("skip_journey", "edit_source_event"),
    "unknown_location": ("correct_location", "skip_journey", "edit_source_event"),
    "unknown_start": ("correct_location", "skip_journey", "edit_source_event"),
    "manual_edit": ("keep_manual_edit", "replace_with_plan", "skip_journey"),
    "manually_deleted": ("recreate_journey", "skip_journey"),
    "hybrid_meeting": ("treat_as_virtual", "treat_as_physical", "skip_journey"),
    "all_day": ("skip_journey", "edit_source_event"),
    "downstream_uncertain": ("skip_journey", "edit_source_event"),
}


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
