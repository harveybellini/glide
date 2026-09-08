"""Live calendar executor: reconciliation over a provider adapter.

Deterministic code owns identity, arithmetic, conflict checks, and writes.
The agent only proposes; this module applies accepted proposals to the
provider travel calendar with conditional writes, ownership markers,
manual-edit detection, and respect for manual deletions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from glide.adapters.interfaces import CalendarAdapter
from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.domain.models import (
    CalendarEvent,
    Decision,
    DecisionStatus,
    JourneyPlan,
    ManagedBlock,
    MutationOperation,
    MutationOutcome,
    MutationReceipt,
    PlaceRef,
    PlanAction,
    Run,
    RunStatus,
    UserSettings,
    WorkflowResult,
)
from glide.domain.scheduling import (
    RouteEstimator,
    block_hash,
    source_fingerprint,
)

DECISION_ACTIONS = {
    "insufficient_time": ("correct_location", "skip_journey", "edit_source_event"),
    "unknown_location": ("correct_location", "skip_journey", "edit_source_event"),
    "unknown_start": ("correct_location", "skip_journey", "edit_source_event"),
    "manual_edit": ("keep_manual_edit", "replace_with_plan", "skip_journey"),
    "manually_deleted": ("recreate_journey", "skip_journey"),
    "hybrid_meeting": ("treat_as_virtual", "treat_as_physical", "skip_journey"),
    "all_day": ("skip_journey", "edit_source_event"),
    "downstream_uncertain": ("correct_location", "skip_journey", "edit_source_event"),
}


class StaleSourceError(RuntimeError):
    """The source calendar changed after it was read; the run must requeue."""


@dataclass
class LiveWorkflow:
    """Maintenance run against a real source and travel calendar."""

    settings: UserSettings
    calendar: CalendarAdapter
    glide_calendar_id: str
    router: RouteEstimator
    runner: AgentRunner = field(default_factory=DeterministicAgentRunner)
    run_sequence: int = 0

    def run(
        self,
        *,
        source_events: list[CalendarEvent],
        place_index: dict[str, PlaceRef],
        now: datetime,
        window_start: datetime,
        window_end: datetime,
        previous_blocks: list[ManagedBlock] | None = None,
        skip_journeys: set[str] | None = None,
        run_id: str | None = None,
    ) -> WorkflowResult:
        self.run_sequence += 1
        run_id = run_id or f"live-run-{self.run_sequence}"
        fingerprint = source_fingerprint(source_events)
        source_events = sorted(source_events, key=lambda event: event.start)

        existing = self.calendar.list_blocks(
            calendar_id=self.glide_calendar_id,
            window_start=window_start,
            window_end=window_end,
        )
        existing = [self._detect_manual_override(block) for block in existing]

        if not self.settings.enabled:
            return WorkflowResult(
                run=Run(
                    id=run_id,
                    user_id=self.settings.user_id,
                    trigger="live",
                    status=RunStatus.PAUSED,
                    lease_revision=1,
                    source_fingerprint=fingerprint,
                    started_at=now,
                    ended_at=now,
                    counts={"source_events": len(source_events), "plans": 0},
                    safe_failure_code=None,
                ),
                plans=(),
                decisions=(),
                travel_blocks=tuple(sorted(existing, key=lambda block: block.start)),
                receipts=(),
                source_events=tuple(source_events),
            )

        plans = self.runner.run(
            settings=self.settings,
            events=source_events,
            place_index=place_index,
            router=self.router,
            now=now,
        )
        skipped = skip_journeys or set()
        if skipped:
            plans = [plan for plan in plans if plan.journey_key not in skipped]

        # Re-read the source dependencies immediately before any mutation. A
        # change between the initial read and here means the plans are stale;
        # the caller requeues and reconciliation keeps the retry harmless.
        current_events = self.calendar.list_events(
            calendar_id=self.settings.source_calendar_id,
            window_start=window_start,
            window_end=window_end,
        )
        if source_fingerprint(current_events) != fingerprint:
            raise StaleSourceError(
                "source calendar changed after it was read; requeueing for "
                "reconciliation"
            )

        blocks = {block.journey_key: block for block in existing}
        previous = {block.journey_key: block for block in (previous_blocks or [])}
        current_keys = {plan.journey_key for plan in plans}
        receipts: list[MutationReceipt] = []
        decisions: list[Decision] = []

        # Remove owned blocks whose journey no longer exists. Past or
        # already-started blocks are never modified.
        for journey_key in sorted(set(blocks) - current_keys):
            block = blocks[journey_key]
            if block.start <= now:
                continue
            self.calendar.delete_block(
                calendar_id=self.glide_calendar_id,
                event_id=block.provider_event_id,
                expected_etag=block.etag,
            )
            blocks.pop(journey_key)
            receipts.append(
                _receipt(
                    run_id=run_id,
                    journey_key=journey_key,
                    operation=MutationOperation.REMOVE,
                    provider_event_id=block.provider_event_id,
                    before_hash=block.last_applied_hash,
                    after_hash=None,
                    outcome=MutationOutcome.APPLIED,
                    timestamp=now,
                )
            )

        for plan in plans:
            existing_block = blocks.get(plan.journey_key)
            if plan.action == PlanAction.CREATE:
                self._apply_create(
                    plan=plan,
                    existing=existing_block,
                    previous=previous.get(plan.journey_key),
                    fingerprint=fingerprint,
                    now=now,
                    run_id=run_id,
                    blocks=blocks,
                    receipts=receipts,
                    decisions=decisions,
                )
                continue

            if (
                existing_block is not None
                and existing_block.start > now
                and plan.action in (PlanAction.REMOVE, PlanAction.DECISION, PlanAction.SKIP)
            ):
                self.calendar.delete_block(
                    calendar_id=self.glide_calendar_id,
                    event_id=existing_block.provider_event_id,
                    expected_etag=existing_block.etag,
                )
                blocks.pop(plan.journey_key)
                receipts.append(
                    _receipt(
                        run_id=run_id,
                        journey_key=plan.journey_key,
                        operation=MutationOperation.REMOVE,
                        provider_event_id=existing_block.provider_event_id,
                        before_hash=existing_block.last_applied_hash,
                        after_hash=None,
                        outcome=MutationOutcome.APPLIED,
                        timestamp=now,
                    )
                )

            if plan.action == PlanAction.DECISION:
                decisions.append(
                    _decision(
                        user_id=self.settings.user_id,
                        fingerprint=fingerprint,
                        occurrence_id=plan.destination_occurrence_id,
                        journey_key=plan.journey_key,
                        reason=plan.reason_code,
                        facts=plan.calculated_facts,
                    )
                )

        counts = {
            "source_events": len(source_events),
            "plans": len(plans),
            "decisions": len(decisions),
            "creates": sum(
                1 for receipt in receipts if receipt.operation == MutationOperation.CREATE
            ),
            "updates": sum(
                1 for receipt in receipts if receipt.operation == MutationOperation.UPDATE
            ),
            "removes": sum(
                1 for receipt in receipts if receipt.operation == MutationOperation.REMOVE
            ),
            "noops": sum(
                1 for receipt in receipts if receipt.operation == MutationOperation.NOOP
            ),
        }
        run = Run(
            id=run_id,
            user_id=self.settings.user_id,
            trigger="live",
            status=RunStatus.NEEDS_INPUT if decisions else RunStatus.COMPLETED,
            lease_revision=1,
            source_fingerprint=fingerprint,
            started_at=now,
            ended_at=now,
            counts=counts,
            safe_failure_code=None,
        )
        return WorkflowResult(
            run=run,
            plans=tuple(plans),
            decisions=tuple(decisions),
            travel_blocks=tuple(sorted(blocks.values(), key=lambda block: block.start)),
            receipts=tuple(receipts),
            source_events=tuple(source_events),
        )

    def _apply_create(
        self,
        *,
        plan: JourneyPlan,
        existing: ManagedBlock | None,
        previous: ManagedBlock | None,
        fingerprint: str,
        now: datetime,
        run_id: str,
        blocks: dict[str, ManagedBlock],
        receipts: list[MutationReceipt],
        decisions: list[Decision],
    ) -> None:
        if plan.proposed_start is None or plan.proposed_end is None:
            raise ValueError("create plans require proposed start and end")

        if existing is not None and existing.manual_override:
            decisions.append(
                _decision(
                    user_id=self.settings.user_id,
                    fingerprint=fingerprint,
                    occurrence_id=plan.destination_occurrence_id,
                    journey_key=plan.journey_key,
                    reason="manual_edit",
                    facts={
                        "existing_start": existing.start.isoformat(),
                        "existing_end": existing.end.isoformat(),
                    },
                )
            )
            return

        new_hash = block_hash(
            start=plan.proposed_start,
            end=plan.proposed_end,
            source_revision=fingerprint,
            padding_minutes=plan.padding_minutes,
        )

        if existing is not None:
            if existing.last_applied_hash == new_hash and not existing.skipped:
                receipts.append(
                    _receipt(
                        run_id=run_id,
                        journey_key=plan.journey_key,
                        operation=MutationOperation.NOOP,
                        provider_event_id=existing.provider_event_id,
                        before_hash=existing.last_applied_hash,
                        after_hash=existing.last_applied_hash,
                        outcome=MutationOutcome.UNCHANGED,
                        timestamp=now,
                    )
                )
                return
            replacement = existing.model_copy(
                update={
                    "user_id": self.settings.user_id,
                    "origin_occurrence_id": plan.origin_occurrence_id,
                    "destination_occurrence_id": plan.destination_occurrence_id,
                    "start": plan.proposed_start,
                    "end": plan.proposed_end,
                    "last_applied_hash": new_hash,
                    "source_revision": fingerprint,
                    "policy_revision": self.settings.revision,
                    "padding_minutes": plan.padding_minutes,
                    "manual_override": False,
                }
            )
            updated = self.calendar.update_block(
                calendar_id=self.glide_calendar_id,
                block=replacement,
                expected_etag=existing.etag,
            )
            blocks[plan.journey_key] = updated
            receipts.append(
                _receipt(
                    run_id=run_id,
                    journey_key=plan.journey_key,
                    operation=MutationOperation.UPDATE,
                    provider_event_id=updated.provider_event_id,
                    before_hash=existing.last_applied_hash,
                    after_hash=new_hash,
                    outcome=MutationOutcome.APPLIED,
                    timestamp=now,
                )
            )
            return

        if previous is not None:
            # A block we previously owned is gone without our delete: the
            # user removed it. Never recreate it immediately.
            decisions.append(
                _decision(
                    user_id=self.settings.user_id,
                    fingerprint=fingerprint,
                    occurrence_id=plan.destination_occurrence_id,
                    journey_key=plan.journey_key,
                    reason="manually_deleted",
                    facts={"occurrence_id": plan.destination_occurrence_id},
                )
            )
            return

        block = ManagedBlock(
            journey_key=plan.journey_key,
            user_id=self.settings.user_id,
            origin_occurrence_id=plan.origin_occurrence_id,
            destination_occurrence_id=plan.destination_occurrence_id,
            provider_event_id="",
            start=plan.proposed_start,
            end=plan.proposed_end,
            last_applied_hash=new_hash,
            etag="",
            source_revision=fingerprint,
            policy_revision=self.settings.revision,
            padding_minutes=plan.padding_minutes,
        )
        created = self.calendar.create_block(
            calendar_id=self.glide_calendar_id,
            block=block,
        )
        blocks[plan.journey_key] = created
        receipts.append(
            _receipt(
                run_id=run_id,
                journey_key=plan.journey_key,
                operation=MutationOperation.CREATE,
                provider_event_id=created.provider_event_id,
                before_hash=None,
                after_hash=new_hash,
                outcome=MutationOutcome.APPLIED,
                timestamp=now,
            )
        )

    def _detect_manual_override(self, block: ManagedBlock) -> ManagedBlock:
        if not block.last_applied_hash:
            return block
        expected = block_hash(
            start=block.start,
            end=block.end,
            source_revision=block.source_revision,
            padding_minutes=block.padding_minutes,
        )
        return block.model_copy(update={"manual_override": block.last_applied_hash != expected})


def _decision(
    *,
    user_id: str,
    fingerprint: str,
    occurrence_id: str,
    journey_key: str,
    reason: str,
    facts: dict[str, int | str | bool],
) -> Decision:
    return Decision(
        id=f"decision-{user_id}-{occurrence_id}-{fingerprint[:16]}",
        user_id=user_id,
        occurrence_id=occurrence_id,
        journey_key=journey_key,
        source_revision=fingerprint,
        reason=reason,
        calculated_facts=facts,
        allowed_actions=DECISION_ACTIONS.get(reason, ("skip_journey",)),
        status=DecisionStatus.OPEN,
        version=1,
    )


def _receipt(
    *,
    run_id: str,
    journey_key: str,
    operation: MutationOperation,
    provider_event_id: str | None,
    before_hash: str | None,
    after_hash: str | None,
    outcome: MutationOutcome,
    timestamp: datetime,
) -> MutationReceipt:
    return MutationReceipt(
        id=f"{run_id}-receipt-{journey_key[:12]}-{operation.value}",
        run_id=run_id,
        journey_key=journey_key,
        operation=operation,
        provider_event_id=provider_event_id,
        before_hash=before_hash,
        after_hash=after_hash,
        outcome=outcome,
        timestamp=timestamp,
    )
