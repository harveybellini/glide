"""Decision lifecycle policy shared by the sample and live processors."""

from __future__ import annotations

from glide.adapters.interfaces import StateStore
from glide.domain.models import DecisionStatus, RunStatus, WorkflowResult


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
