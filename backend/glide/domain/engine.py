"""Sample workflow orchestration.

This is intentionally a small deterministic executor over the frozen domain
contracts. It shows the create/update/remove loop before live Google adapters
exist, and it is the foundation that the real executor will extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.domain.decisions import ADD_ANYWAY, apply_forced_journeys
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
from glide.domain.scheduling import RouteEstimator, block_hash, source_fingerprint


@dataclass
class InMemoryTravelCalendar:
    """Owned travel blocks for an isolated sample tenant."""

    blocks: dict[str, ManagedBlock] = field(default_factory=dict)

    def get(self, journey_key: str) -> ManagedBlock | None:
        return self.blocks.get(journey_key)

    def put(self, block: ManagedBlock) -> None:
        self.blocks[block.journey_key] = block

    def delete(self, journey_key: str) -> ManagedBlock | None:
        return self.blocks.pop(journey_key, None)

    def all(self) -> list[ManagedBlock]:
        return sorted(self.blocks.values(), key=lambda block: block.start)

    def mark_manual_override(self, journey_key: str, start: datetime, end: datetime) -> None:
        block = self.blocks.get(journey_key)
        if block is None:
            raise KeyError(f"no managed block for journey {journey_key}")
        self.blocks[journey_key] = block.model_copy(
            update={
                "start": start,
                "end": end,
                "manual_override": True,
                "last_applied_hash": "user-edited",
            }
        )


@dataclass
class SampleWorkflow:
    settings: UserSettings
    travel_calendar: InMemoryTravelCalendar
    router: RouteEstimator
    runner: AgentRunner = field(default_factory=DeterministicAgentRunner)
    run_sequence: int = 0

    def run(
        self,
        *,
        source_events: list[CalendarEvent],
        place_index: dict[str, PlaceRef],
        now: datetime,
        skip_journeys: set[str] | None = None,
        force_journeys: set[str] | None = None,
        run_id: str | None = None,
    ) -> WorkflowResult:
        self.run_sequence += 1
        run_id = run_id or f"sample-run-{self.run_sequence}"
        started_at = now
        fingerprint = source_fingerprint(source_events)

        if not self.settings.enabled:
            return WorkflowResult(
                run=Run(
                    id=run_id,
                    user_id=self.settings.user_id,
                    trigger="sample",
                    status=RunStatus.PAUSED,
                    lease_revision=1,
                    source_fingerprint=fingerprint,
                    started_at=started_at,
                    ended_at=now,
                    counts={"source_events": len(source_events), "plans": 0},
                    safe_failure_code=None,
                ),
                plans=(),
                decisions=(),
                travel_blocks=tuple(self.travel_calendar.all()),
                receipts=(),
                source_events=tuple(sorted(source_events, key=lambda event: event.start)),
            )

        plans = self.runner.run(
            settings=self.settings,
            events=source_events,
            place_index=place_index,
            router=self.router,
            now=now,
        )
        plans = apply_forced_journeys(
            plans,
            forced_journeys=force_journeys or set(),
            events=source_events,
            now=now,
        )
        skip_journeys = skip_journeys or set()
        if skip_journeys:
            plans = [plan for plan in plans if plan.journey_key not in skip_journeys]

        current_keys = {plan.journey_key for plan in plans}
        receipts: list[MutationReceipt] = []
        decisions: list[Decision] = []

        # Remove owned blocks whose journey no longer exists.
        for journey_key in sorted(set(self.travel_calendar.blocks) - current_keys):
            removed = self.travel_calendar.delete(journey_key)
            if removed is not None:
                receipts.append(
                    _receipt(
                        run_id=run_id,
                        journey_key=journey_key,
                        operation=MutationOperation.REMOVE,
                        before_hash=removed.last_applied_hash,
                        after_hash=None,
                        outcome=MutationOutcome.APPLIED,
                        timestamp=now,
                    )
                )

        materialized_plans: list[JourneyPlan] = []
        for plan in plans:
            if plan.action == PlanAction.CREATE:
                existing = self.travel_calendar.get(plan.journey_key)
                if existing is not None and existing.manual_override:
                    plan = plan.model_copy(
                        update={
                            "action": PlanAction.DECISION,
                            "reason_code": "manual_edit",
                            "calculated_facts": {
                                "existing_start": existing.start.isoformat(),
                                "existing_end": existing.end.isoformat(),
                            },
                        }
                    )
            materialized_plans.append(plan)

        for plan in materialized_plans:
            if plan.action == PlanAction.CREATE:
                _apply_create(
                    plan=plan,
                    settings=self.settings,
                    source_revision=fingerprint,
                    store=self.travel_calendar,
                    receipts=receipts,
                    timestamp=now,
                    run_id=run_id,
                )
            elif plan.action in (PlanAction.REMOVE, PlanAction.DECISION, PlanAction.SKIP):
                if plan.reason_code != "manual_edit":
                    removed = self.travel_calendar.delete(plan.journey_key)
                    if removed is not None:
                        receipts.append(
                            _receipt(
                                run_id=run_id,
                                journey_key=plan.journey_key,
                                operation=MutationOperation.REMOVE,
                                before_hash=removed.last_applied_hash,
                                after_hash=None,
                                outcome=MutationOutcome.APPLIED,
                                timestamp=now,
                            )
                        )

            if plan.action == PlanAction.DECISION:
                decisions.append(
                    Decision(
                        id=(
                            f"decision-{self.settings.user_id}-"
                            f"{plan.destination_occurrence_id}-{fingerprint[:16]}"
                        ),
                        user_id=self.settings.user_id,
                        occurrence_id=plan.destination_occurrence_id,
                        journey_key=plan.journey_key,
                        source_revision=fingerprint,
                        reason=plan.reason_code,
                        calculated_facts=plan.calculated_facts,
                        allowed_actions=(
                            (
                                ADD_ANYWAY,
                                "correct_location",
                                "skip_journey",
                                "edit_source_event",
                            )
                            if plan.reason_code == "insufficient_time"
                            else (
                                "correct_location",
                                "skip_journey",
                                "edit_source_event",
                            )
                        ),
                        status=DecisionStatus.OPEN,
                        version=1,
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
        }
        run = Run(
            id=run_id,
            user_id=self.settings.user_id,
            trigger="sample",
            status=RunStatus.NEEDS_INPUT if decisions else RunStatus.COMPLETED,
            lease_revision=1,
            source_fingerprint=fingerprint,
            started_at=started_at,
            ended_at=now,
            counts=counts,
            safe_failure_code=None,
        )
        return WorkflowResult(
            run=run,
            plans=tuple(plans),
            decisions=tuple(decisions),
            travel_blocks=tuple(self.travel_calendar.all()),
            receipts=tuple(receipts),
            source_events=tuple(sorted(source_events, key=lambda event: event.start)),
        )


def _apply_create(
    *,
    plan: JourneyPlan,
    settings: UserSettings,
    source_revision: str,
    store: InMemoryTravelCalendar,
    receipts: list[MutationReceipt],
    timestamp: datetime,
    run_id: str,
) -> None:
    if plan.proposed_start is None or plan.proposed_end is None:
        raise ValueError("create plans require proposed start and end")

    new_hash = block_hash(
        start=plan.proposed_start,
        end=plan.proposed_end,
        source_revision=source_revision,
        padding_minutes=plan.padding_minutes,
    )
    existing = store.get(plan.journey_key)

    if existing is not None and existing.last_applied_hash == new_hash:
        receipts.append(
            _receipt(
                run_id=run_id,
                journey_key=plan.journey_key,
                operation=MutationOperation.NOOP,
                before_hash=existing.last_applied_hash,
                after_hash=existing.last_applied_hash,
                outcome=MutationOutcome.UNCHANGED,
                timestamp=timestamp,
            )
        )
        return

    operation = MutationOperation.UPDATE if existing is not None else MutationOperation.CREATE
    block = ManagedBlock(
        journey_key=plan.journey_key,
        user_id=settings.user_id,
        origin_occurrence_id=plan.origin_occurrence_id,
        destination_occurrence_id=plan.destination_occurrence_id,
        provider_event_id=f"travel-{plan.journey_key[:16]}",
        start=plan.proposed_start,
        end=plan.proposed_end,
        last_applied_hash=new_hash,
        etag=f"fixture-travel-etag-{run_id}",
        source_revision=source_revision,
        policy_revision=settings.revision,
        padding_minutes=plan.padding_minutes,
        manual_override=False,
        skipped=False,
    )
    store.put(block)
    receipts.append(
        _receipt(
            run_id=run_id,
            journey_key=plan.journey_key,
            operation=operation,
            before_hash=existing.last_applied_hash if existing else None,
            after_hash=new_hash,
            outcome=MutationOutcome.APPLIED,
            timestamp=timestamp,
        )
    )


def _receipt(
    *,
    run_id: str,
    journey_key: str,
    operation: MutationOperation,
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
        provider_event_id=None,
        before_hash=before_hash,
        after_hash=after_hash,
        outcome=outcome,
        timestamp=timestamp,
    )
