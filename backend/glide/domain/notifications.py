"""Once-only "needs your decision" announcements.

Glide reconciles on a schedule, so the same open decision is re-observed on
every poll. Announcing it on each observation would spam the user, and the
whole point of the product is that it stays quiet until a real decision is
required. ``Decision.notified_at`` is the durable dedupe mark.

Two steps keep that mark honest:

* :func:`carry_notification_state` copies the persisted mark onto the fresh
  decision objects a run just produced, because each run rebuilds decisions
  from the source calendar and would otherwise erase the fact that the user
  has already been told.
* :func:`deliver_open_decisions` sends only decisions that are still unmarked,
  then persists the mark. A transport failure leaves the decision unmarked so
  the next scheduled run retries it, which is the safe direction to fail.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from glide.adapters.interfaces import DecisionNotifier, StateStore
from glide.domain.models import (
    Decision,
    DecisionStatus,
    RunStatus,
    WorkflowResult,
)

logger = logging.getLogger("glide.notifications")


def carry_notification_state(
    state_store: StateStore,
    result: WorkflowResult,
) -> WorkflowResult:
    """Preserve persisted notification marks across a fresh run result."""

    stored = {
        decision.id: decision
        for decision in state_store.get_decisions(result.run.user_id)
    }
    decisions = tuple(
        decision.model_copy(
            update={"notified_at": stored[decision.id].notified_at}
        )
        if decision.id in stored and stored[decision.id].notified_at is not None
        else decision
        for decision in result.decisions
    )
    if decisions == result.decisions:
        return result
    return result.model_copy(update={"decisions": decisions})


def deliver_open_decisions(
    state_store: StateStore,
    notifier: DecisionNotifier,
    result: WorkflowResult,
    *,
    now: datetime | None = None,
) -> list[Decision]:
    """Announce newly open decisions, stamp them, and return the stamped rows."""

    if result.run.status not in (RunStatus.COMPLETED, RunStatus.NEEDS_INPUT):
        return []
    settings = state_store.get_settings(result.run.user_id)
    if (
        settings is None
        or not settings.notify_on_decisions
        or not settings.notification_email
    ):
        return []
    stamped_at = now or datetime.now(UTC)
    stamped: list[Decision] = []
    for decision in result.decisions:
        if (
            decision.status is not DecisionStatus.OPEN
            or decision.notified_at is not None
        ):
            continue
        try:
            notifier.send_decision_opened(settings=settings, decision=decision)
        except Exception as exc:  # noqa: BLE001 - a failed message must not fail a run
            # Never log the address or subject: identifier plus error type is
            # enough to find this in CloudWatch without copying user data.
            logger.warning(
                "decision=<%s> | notification failed (%s); will retry on the next run",
                decision.id,
                type(exc).__name__,
            )
            continue
        stamped.append(decision.model_copy(update={"notified_at": stamped_at}))
    if stamped:
        state_store.save_decisions(stamped)
    return stamped
