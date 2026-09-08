"""Deterministic tool host bound to one run of one tenant.

The Strands agent may observe and propose, but every reference, arithmetic
result, budget, and decision request passes through this host. It deliberately
has no Strands import so every path can be exercised synchronously in tests.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from glide.adapters.interfaces import PlaceLookup
from glide.agent.tools import (
    EstimateJourneyInput,
    EstimateJourneyOutput,
    EvaluateCandidateInput,
    EvaluateCandidateOutput,
    JourneyPair,
    LookupPlaceInput,
    LookupPlaceOutput,
    PlaceCandidate,
    PlannedJourney,
    ProposePlanInput,
    ProposePlanOutput,
    ReadScheduleInput,
    ReadScheduleOutput,
    RequestDecisionInput,
    RequestDecisionOutput,
    ScheduleEvent,
)
from glide.domain.models import (
    CalendarEvent,
    EventKind,
    JourneyPlan,
    PlaceRef,
    PlanAction,
    RouteEstimate,
    TravelMode,
    UserSettings,
)
from glide.domain.scheduling import (
    RouteEstimator,
    busy_intervals,
    ensure_utc,
    evaluate_journey_candidate,
    journey_pairs,
    physical_events,
    places_match,
)

logger = logging.getLogger("glide.agent")

ALLOWED_DECISION_ACTIONS = ("correct_location", "skip_journey", "edit_source_event")
MODEL_DECISION_REASONS = (
    "insufficient_time",
    "unknown_location",
    "unknown_start",
    "no_route",
    "hybrid_meeting",
    "all_day",
    "downstream_uncertain",
)


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    outcome: str
    reason_code: str
    duration_ms: int


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    journey_key: str
    occurrence_id: str
    reason_code: str
    facts: dict[str, int | str | bool]
    allowed_actions: tuple[str, ...]


@dataclass
class ToolHost:
    """Server-bound state and deterministic implementations for six tools."""

    settings: UserSettings
    events: list[CalendarEvent]
    place_index: dict[str, PlaceRef]
    router: RouteEstimator
    now: datetime
    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex}")
    max_events: int = 20
    max_journeys: int = 10
    max_routes: int = 30
    place_search: PlaceLookup | None = None

    estimates: dict[str, RouteEstimate] = field(default_factory=dict, init=False)
    route_calls: int = field(default=0, init=False)
    unavailable_routes: set[tuple[str, str]] = field(default_factory=set, init=False)
    proposal: tuple[PlannedJourney, ...] | None = field(default=None, init=False)
    decision_requests: dict[str, DecisionRequest] = field(default_factory=dict, init=False)
    last_rejection: str | None = field(default=None, init=False)
    tool_log: list[ToolCallRecord] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.now = ensure_utc(self.now)

    # ------------------------------------------------------------------ views

    @property
    def physical(self) -> list[CalendarEvent]:
        return physical_events(self.events)

    @property
    def busy(self):
        return busy_intervals(self.events)

    @property
    def all_pairs(self) -> list[JourneyPair]:
        refs = journey_pairs(
            settings=self.settings,
            events=self.events,
            place_index=self.place_index,
            now=self.now,
        )
        return [_pair_to_tool_contract(ref) for ref in refs]

    @property
    def shown_pairs(self) -> list[JourneyPair]:
        return self.all_pairs[: self.max_journeys]

    @property
    def scope_exceeded(self) -> bool:
        return len(self.physical) > self.max_events or len(self.all_pairs) > self.max_journeys

    @property
    def window_start(self) -> datetime:
        candidates = [self.now, *[event.start for event in self.events]]
        return min(candidates) - timedelta(hours=1)

    @property
    def window_end(self) -> datetime:
        candidates = [self.now, *[event.end for event in self.events]]
        return max(candidates) + timedelta(hours=1)

    def known_place_refs(self) -> list[PlaceRef]:
        refs: dict[str, PlaceRef] = {}
        if self.settings.start_place is not None:
            refs[self.settings.start_place.id] = self.settings.start_place
        for ref in self.place_index.values():
            refs.setdefault(ref.id, ref)
        return [refs[key] for key in sorted(refs)]

    def place_for(self, occurrence_id: str) -> PlaceRef | None:
        if occurrence_id == "start_place":
            return self.settings.start_place
        return self.place_index.get(occurrence_id)

    def pair_for(self, journey_key: str) -> JourneyPair:
        for pair in self.all_pairs:
            if pair.journey_key == journey_key:
                return pair
        raise ValueError(f"unknown journey key {journey_key!r}")

    # ------------------------------------------------------------------ tools

    def read_schedule(
        self,
        *,
        run_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> ReadScheduleOutput:
        input_data = ReadScheduleInput(
            run_id=run_id,
            window_start=window_start,
            window_end=window_end,
        )
        if input_data.run_id != self.run_id:
            raise ValueError("run_id is server-bound; use the run id supplied in the task")
        return self._record(
            "read_schedule",
            lambda: self._read_schedule(
                ensure_utc(input_data.window_start),
                ensure_utc(input_data.window_end),
            ),
            ok_code="schedule_read",
        )

    def _read_schedule(
        self,
        window_start: datetime,
        window_end: datetime,
    ) -> ReadScheduleOutput:
        start = max(window_start, self.window_start)
        end = min(window_end, self.window_end)
        if start >= end:
            return ReadScheduleOutput(events=[], busy_intervals=[])

        events = [
            ScheduleEvent(
                occurrence_id=event.occurrence_id,
                start=event.start,
                end=event.end,
                title=event.title,
                location=event.location,
                kind=event.kind.value,
                status=event.status.value,
                transparency=event.transparency.value,
            )
            for event in self.physical
            if start <= event.start < end
        ][: self.max_events]
        return ReadScheduleOutput(
            events=events,
            busy_intervals=[(interval.start, interval.end) for interval in self.busy],
            journey_pairs=self.shown_pairs,
            scope_exceeded=self.scope_exceeded,
        )

    def lookup_place(
        self,
        *,
        query: str,
        region: str | None = None,
    ) -> LookupPlaceOutput:
        input_data = LookupPlaceInput(query=query, region=region)
        return self._record(
            "lookup_place",
            lambda: self._lookup_place(input_data.query, input_data.region),
            ok_code="resolved" if self._exact_match(input_data.query) else "candidates",
        )

    def _exact_match(self, query: str) -> bool:
        normalized = query.strip().lower()
        return any(
            ref.label.strip().lower() == normalized or ref.provider_id == query.strip()
            for ref in self.known_place_refs()
        )

    def _lookup_place(self, query: str, region: str | None) -> LookupPlaceOutput:
        if self.place_search is not None:
            refs = self.place_search.search(
                query=query,
                region=region,
                storage_allowed=False,
            )
            return LookupPlaceOutput(
                candidates=[_place_candidate(ref) for ref in refs[:3]],
                reason=None,
            )

        normalized = query.strip().lower()
        refs = self.known_place_refs()
        exact = [
            ref
            for ref in refs
            if ref.label.strip().lower() == normalized or ref.provider_id == query.strip()
        ]
        if exact:
            return LookupPlaceOutput(
                candidates=[_place_candidate(exact[0], confirmed=True)],
                confirmed_alias=_place_candidate(exact[0], confirmed=True),
                reason=None,
            )
        fuzzy = [
            ref
            for ref in refs
            if normalized in ref.label.strip().lower()
            or ref.label.strip().lower() in normalized
        ]
        if fuzzy:
            return LookupPlaceOutput(
                candidates=[_place_candidate(ref) for ref in fuzzy[:3]],
                reason=None,
            )
        return LookupPlaceOutput(
            candidates=[],
            reason=f"no matching place found for {query!r}",
        )

    def estimate_journey(
        self,
        *,
        origin_place_id: str,
        destination_place_id: str,
        mode: str,
        timing: str,
        timing_time: datetime,
    ) -> EstimateJourneyOutput:
        input_data = EstimateJourneyInput(
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            mode=mode,
            timing=timing,
            timing_time=timing_time,
        )
        known_ids = {ref.id for ref in self.known_place_refs()}
        if input_data.origin_place_id not in known_ids:
            raise ValueError(f"unknown origin place reference {input_data.origin_place_id!r}")
        if input_data.destination_place_id not in known_ids:
            raise ValueError(
                f"unknown destination place reference {input_data.destination_place_id!r}"
            )
        return self._record(
            "estimate_journey",
            lambda: self._estimate_journey(input_data),
            ok_code="estimated",
        )

    def _estimate_journey(self, input_data: EstimateJourneyInput) -> EstimateJourneyOutput:
        if self.route_calls >= self.max_routes:
            return _unavailable_estimate(
                origin_place_id=input_data.origin_place_id,
                destination_place_id=input_data.destination_place_id,
                reason="route_budget_exceeded",
            )

        constraint_time = ensure_utc(input_data.timing_time)
        self.route_calls += 1
        try:
            estimate = self.router.estimate(
                origin_place_id=input_data.origin_place_id,
                destination_place_id=input_data.destination_place_id,
                mode=TravelMode.DRIVING,
                departure_at=constraint_time if input_data.timing == "depart_at" else None,
                arrival_by=constraint_time if input_data.timing == "arrive_by" else None,
            )
        except Exception as exc:  # noqa: BLE001 - provider failures become typed results
            logger.info(
                "run_id=<%s> | route unavailable | origin=<%s> destination=<%s> error=<%s>",
                self.run_id,
                input_data.origin_place_id,
                input_data.destination_place_id,
                type(exc).__name__,
            )
            self.unavailable_routes.add(
                (input_data.origin_place_id, input_data.destination_place_id)
            )
            return _unavailable_estimate(
                origin_place_id=input_data.origin_place_id,
                destination_place_id=input_data.destination_place_id,
                reason="no_route",
            )

        self.estimates[estimate.id] = estimate
        return EstimateJourneyOutput(
            estimate_id=estimate.id,
            duration_seconds=estimate.duration_seconds,
            provider=estimate.provider,
            observed_at=estimate.observed_at,
            available=True,
        )

    def evaluate_candidate(
        self,
        *,
        origin_available: datetime,
        destination_start: datetime,
        estimate_id: str,
        padding_minutes: int,
    ) -> EvaluateCandidateOutput:
        input_data = EvaluateCandidateInput(
            origin_available=origin_available,
            destination_start=destination_start,
            estimate_id=estimate_id,
            padding_minutes=padding_minutes,
        )
        estimate = self.estimates.get(input_data.estimate_id)
        if estimate is None:
            raise ValueError(f"unknown route estimate reference {input_data.estimate_id!r}")
        return self._record(
            "evaluate_candidate",
            lambda: self._evaluate_candidate(input_data, estimate),
            ok_code="evaluated",
        )

    def _evaluate_candidate(
        self,
        input_data: EvaluateCandidateInput,
        estimate: RouteEstimate,
    ) -> EvaluateCandidateOutput:
        evaluation = evaluate_journey_candidate(
            origin_available=input_data.origin_available,
            destination_start=input_data.destination_start,
            duration_seconds=estimate.duration_seconds,
            padding_minutes=input_data.padding_minutes,
            busy=self.busy,
        )
        return EvaluateCandidateOutput(
            feasible=evaluation.feasible,
            proposed_start=evaluation.proposed_start,
            proposed_end=evaluation.proposed_end,
            wait_seconds=evaluation.wait_seconds,
            available_seconds=evaluation.available_seconds,
            required_seconds=evaluation.required_seconds,
            shortfall_seconds=evaluation.shortfall_seconds,
            reason_code=evaluation.reason_code,
        )

    def request_decision(
        self,
        *,
        journey_key: str,
        occurrence_id: str,
        reason_code: str,
        facts: dict[str, int | str | bool],
        allowed_actions: list[str],
    ) -> RequestDecisionOutput:
        input_data = RequestDecisionInput(
            journey_key=journey_key,
            occurrence_id=occurrence_id,
            reason_code=reason_code,
            facts=facts,
            allowed_actions=allowed_actions,
        )
        pair = self.pair_for(input_data.journey_key)
        if pair.destination_occurrence_id != input_data.occurrence_id:
            raise ValueError(
                f"decision occurrence {input_data.occurrence_id!r} does not match "
                f"journey destination {pair.destination_occurrence_id!r}"
            )
        unexpected = set(input_data.allowed_actions) - set(ALLOWED_DECISION_ACTIONS)
        if unexpected:
            raise ValueError(f"decision actions not permitted: {sorted(unexpected)}")
        return self._record(
            "request_decision",
            lambda: self._request_decision(input_data),
            ok_code="decision_requested",
        )

    def _request_decision(self, input_data: RequestDecisionInput) -> RequestDecisionOutput:
        existing = self.decision_requests.get(input_data.journey_key)
        if existing is not None:
            return RequestDecisionOutput(decision_id=existing.decision_id, status="open")
        request = DecisionRequest(
            decision_id=f"{self.run_id}-decision-{input_data.occurrence_id}",
            journey_key=input_data.journey_key,
            occurrence_id=input_data.occurrence_id,
            reason_code=input_data.reason_code,
            facts=dict(input_data.facts),
            allowed_actions=tuple(input_data.allowed_actions),
        )
        self.decision_requests[input_data.journey_key] = request
        return RequestDecisionOutput(decision_id=request.decision_id, status="open")

    def accept_proposal(self, proposal: ProposePlanInput) -> ProposePlanOutput:
        def rejected(reason: str) -> ProposePlanOutput:
            self.last_rejection = reason
            return ProposePlanOutput(accepted=False, journeys=[], reason=reason)

        if proposal.run_id != self.run_id:
            return rejected("run_id is server-bound; use the run id supplied in the task")
        if self.proposal is not None:
            return rejected("a proposal has already been accepted for this run")

        expected = {pair.journey_key for pair in self.shown_pairs}
        submitted = [journey.journey_key for journey in proposal.journeys]
        problems: list[str] = []
        if len(set(submitted)) != len(submitted):
            problems.append("duplicate journey keys")
        missing = expected - set(submitted)
        if missing:
            problems.append(f"missing journeys: {', '.join(sorted(missing))}")
        unknown = set(submitted) - expected
        if unknown:
            problems.append(f"unknown journeys: {', '.join(sorted(unknown))}")
        if problems:
            return rejected("; ".join(problems))

        journeys_by_key = {journey.journey_key: journey for journey in proposal.journeys}
        chain_blocked = False
        for pair in self.shown_pairs:
            journey = journeys_by_key[pair.journey_key]
            problem = self._validate_journey(journey, pair, chain_blocked)
            if problem is not None:
                return rejected(problem)
            chain_blocked = chain_blocked or journey.reason_code in (
                "unknown_location",
                "insufficient_time",
                "downstream_uncertain",
            )

        self.proposal = tuple(proposal.journeys)
        return ProposePlanOutput(accepted=True, journeys=list(proposal.journeys))

    def _validate_journey(
        self,
        journey: PlannedJourney,
        pair: JourneyPair,
        chain_blocked: bool,
    ) -> str | None:
        if (
            journey.origin_occurrence_id != pair.origin_occurrence_id
            or journey.destination_occurrence_id != pair.destination_occurrence_id
        ):
            return (
                f"journey {pair.journey_key} origin/destination does not match the "
                "server-supplied pair"
            )

        origin = self.place_for(pair.origin_occurrence_id)
        destination = self.place_for(pair.destination_occurrence_id)
        origin_id = origin.id if origin is not None else None
        destination_id = destination.id if destination is not None else None

        if journey.action == PlanAction.CREATE:
            if journey.reason_code != "feasible":
                return "create journeys require reason_code 'feasible'"
            if origin_id is None or destination_id is None:
                return "cannot create a journey with an unresolved place"
            estimate = self.estimates.get(journey.route_estimate_id or "")
            if estimate is None:
                return f"unknown route estimate reference {journey.route_estimate_id!r}"
            if (estimate.origin_place_id, estimate.destination_place_id) != (
                origin_id,
                destination_id,
            ):
                return "route estimate places do not match this journey"
            evaluation = self._evaluate_pair(pair, estimate)
            if not evaluation.feasible:
                return (
                    "deterministic evaluation found this journey infeasible; "
                    "emit a decision with reason_code 'insufficient_time' instead"
                )

        elif journey.action == PlanAction.REMOVE:
            if journey.reason_code != "same_place":
                return "remove journeys require reason_code 'same_place'"
            if journey.route_estimate_id not in (None, "same-place"):
                return "remove journeys must not reference a timed route estimate"
            if origin is None or destination is None:
                return "same_place requires both places to resolve"
            if not places_match(origin, destination):
                return "origin and destination do not resolve to the same place"

        elif journey.action == PlanAction.DECISION:
            if journey.reason_code not in MODEL_DECISION_REASONS:
                return f"reason_code {journey.reason_code!r} is not a model decision"
            if journey.reason_code == "insufficient_time":
                estimate = self.estimates.get(journey.route_estimate_id or "")
                if estimate is None:
                    return "insufficient_time requires a timed route estimate"
                if (estimate.origin_place_id, estimate.destination_place_id) != (
                    origin_id,
                    destination_id,
                ):
                    return "route estimate places do not match this journey"
                if self._evaluate_pair(pair, estimate).feasible:
                    return (
                        "deterministic evaluation found this journey feasible; "
                        "emit a create journey instead"
                    )
            elif journey.reason_code == "unknown_location":
                if origin is not None and destination is not None:
                    return "both places resolve, so unknown_location is not justified"
            elif journey.reason_code == "unknown_start":
                if (
                    pair.origin_occurrence_id != "start_place"
                    or self.settings.start_place is not None
                ):
                    return "unknown_start is only valid when the start address is missing"
            elif journey.reason_code == "no_route":
                if origin_id is None or destination_id is None:
                    return "no_route is only valid between two resolved places"
                if (origin_id, destination_id) not in self.unavailable_routes:
                    return "no route failure was observed for this journey"
            elif journey.reason_code == "hybrid_meeting":
                if pair.origin_occurrence_id != pair.destination_occurrence_id:
                    return "hybrid_meeting is only valid for a self journey"
                event = next(
                    (
                        candidate
                        for candidate in self.events
                        if candidate.occurrence_id == pair.destination_occurrence_id
                    ),
                    None,
                )
                if (
                    event is None
                    or event.kind != EventKind.UNKNOWN
                    or not (event.location or "").strip()
                ):
                    return "hybrid_meeting requires an uncertain meeting with a location"
            elif journey.reason_code == "all_day":
                if pair.origin_occurrence_id != pair.destination_occurrence_id:
                    return "all_day is only valid for a self journey"
                event = next(
                    (
                        candidate
                        for candidate in self.events
                        if candidate.occurrence_id == pair.destination_occurrence_id
                    ),
                    None,
                )
                if event is None or not event.all_day:
                    return "all_day requires an opaque all-day event"
            elif journey.reason_code == "downstream_uncertain":
                if not chain_blocked:
                    return "downstream_uncertain requires an unresolved upstream journey"
        else:
            return f"action {journey.action!r} is not permitted for a model proposal"

        for key, value in journey.evidence.items():
            if not isinstance(value, (int, str, bool)):
                return f"evidence {key!r} must be an int, string, or boolean"
        return None

    def _evaluate_pair(self, pair: JourneyPair, estimate: RouteEstimate):
        return evaluate_journey_candidate(
            origin_available=pair.origin_available,
            destination_start=pair.destination_start,
            duration_seconds=estimate.duration_seconds,
            padding_minutes=self.settings.padding_minutes,
            busy=self.busy,
        )

    # ------------------------------------------------------------- materialize

    def materialize_plans(self) -> list[JourneyPlan]:
        if self.proposal is None:
            raise ValueError("no accepted proposal to materialize")
        source_etags = {event.occurrence_id: event.etag for event in self.events}
        accepted = {journey.journey_key: journey for journey in self.proposal}
        plans: list[JourneyPlan] = []
        for index, pair in enumerate(self.all_pairs):
            if index >= self.max_journeys:
                plans.append(
                    self._decision_plan(
                        pair,
                        reason_code="scope_exceeded",
                        source_etags=source_etags,
                        facts={"occurrence_id": pair.destination_occurrence_id},
                    )
                )
                continue
            plans.append(
                self._materialize_journey(accepted[pair.journey_key], pair, source_etags)
            )
        return plans

    def _materialize_journey(
        self,
        journey: PlannedJourney,
        pair: JourneyPair,
        source_etags: dict[str, str],
    ) -> JourneyPlan:
        common = {
            "journey_key": pair.journey_key,
            "origin_occurrence_id": pair.origin_occurrence_id,
            "destination_occurrence_id": pair.destination_occurrence_id,
            "source_calendar_id": self.settings.source_calendar_id,
            "source_etags": source_etags,
            "padding_minutes": self.settings.padding_minutes,
        }
        if journey.action == PlanAction.CREATE:
            estimate = self.estimates[journey.route_estimate_id or ""]
            evaluation = self._evaluate_pair(pair, estimate)
            return JourneyPlan(
                **common,
                route_estimate_id=estimate.id,
                proposed_start=evaluation.proposed_start,
                proposed_end=evaluation.proposed_end,
                action=PlanAction.CREATE,
                reason_code="feasible",
                calculated_facts={
                    "duration_seconds": estimate.duration_seconds,
                    "padding_minutes": self.settings.padding_minutes,
                    "wait_seconds": evaluation.wait_seconds,
                },
            )
        if journey.action == PlanAction.REMOVE:
            return JourneyPlan(
                **common,
                route_estimate_id="same-place",
                action=PlanAction.REMOVE,
                reason_code="same_place",
                calculated_facts={"duration_seconds": 0},
            )

        facts = self._decision_facts(journey, pair)
        request = self.decision_requests.get(pair.journey_key)
        if request is not None:
            # Deterministic facts always win over model-supplied evidence.
            facts = {**request.facts, **facts}
        return JourneyPlan(
            **common,
            route_estimate_id=journey.route_estimate_id or "unavailable",
            action=PlanAction.DECISION,
            reason_code=journey.reason_code,
            calculated_facts=facts,
        )

    def _decision_facts(
        self,
        journey: PlannedJourney,
        pair: JourneyPair,
    ) -> dict[str, int | str | bool]:
        if journey.reason_code == "insufficient_time":
            estimate = self.estimates[journey.route_estimate_id or ""]
            evaluation = self._evaluate_pair(pair, estimate)
            return {
                "available_seconds": evaluation.available_seconds,
                "required_seconds": evaluation.required_seconds,
                "shortfall_seconds": evaluation.shortfall_seconds,
                "duration_seconds": estimate.duration_seconds,
            }
        if journey.reason_code == "unknown_location":
            return {
                "origin_occurrence_id": pair.origin_occurrence_id,
                "destination_occurrence_id": pair.destination_occurrence_id,
            }
        if journey.reason_code == "no_route":
            origin = self.place_for(pair.origin_occurrence_id)
            destination = self.place_for(pair.destination_occurrence_id)
            return {
                "origin_place_id": origin.id if origin else "",
                "destination_place_id": destination.id if destination else "",
            }
        if journey.reason_code == "hybrid_meeting":
            return {
                "occurrence_id": pair.destination_occurrence_id,
                "location": pair.destination_location or "",
            }
        if journey.reason_code == "all_day":
            return {"occurrence_id": pair.destination_occurrence_id}
        if journey.reason_code == "downstream_uncertain":
            return {
                "origin_occurrence_id": pair.origin_occurrence_id,
                "destination_occurrence_id": pair.destination_occurrence_id,
            }
        return {}

    def _decision_plan(
        self,
        pair: JourneyPair,
        *,
        reason_code: str,
        source_etags: dict[str, str],
        facts: dict[str, int | str | bool],
    ) -> JourneyPlan:
        return JourneyPlan(
            journey_key=pair.journey_key,
            origin_occurrence_id=pair.origin_occurrence_id,
            destination_occurrence_id=pair.destination_occurrence_id,
            source_calendar_id=self.settings.source_calendar_id,
            source_etags=source_etags,
            route_estimate_id="unavailable",
            padding_minutes=self.settings.padding_minutes,
            action=PlanAction.DECISION,
            reason_code=reason_code,
            calculated_facts=facts,
        )

    # ------------------------------------------------------------- bookkeeping

    def _record(self, name: str, operation, ok_code: str):
        started = time.monotonic()
        try:
            result = operation()
            self._append_record(
                ToolCallRecord(
                    name=name,
                    outcome="ok",
                    reason_code=ok_code,
                    duration_ms=_elapsed_ms(started),
                )
            )
            return result
        except ValueError as exc:
            self._append_record(
                ToolCallRecord(
                    name=name,
                    outcome="error",
                    reason_code="invalid_input",
                    duration_ms=_elapsed_ms(started),
                )
            )
            raise ValueError(str(exc)) from exc

    def _append_record(self, record: ToolCallRecord) -> None:
        self.tool_log.append(record)
        logger.info(
            "run_id=<%s> tool=<%s> outcome=<%s> reason=<%s> duration_ms=<%d>",
            self.run_id,
            record.name,
            record.outcome,
            record.reason_code,
            record.duration_ms,
        )


def _pair_to_tool_contract(pair) -> JourneyPair:
    return JourneyPair(
        journey_key=pair.journey_key,
        origin_occurrence_id=pair.origin_occurrence_id,
        destination_occurrence_id=pair.destination_occurrence_id,
        origin_location=pair.origin_location,
        destination_location=pair.destination_location,
        origin_available=pair.origin_available,
        destination_start=pair.destination_start,
        destination_arrival_target=pair.destination_arrival_target,
    )


def _place_candidate(ref: PlaceRef, confirmed: bool = False) -> PlaceCandidate:
    return PlaceCandidate(
        id=ref.id,
        provider_id=ref.provider_id,
        label=ref.label,
        longitude=ref.longitude,
        latitude=ref.latitude,
        provenance=ref.provenance,
        confirmed=confirmed,
    )


def _unavailable_estimate(
    *,
    origin_place_id: str,
    destination_place_id: str,
    reason: str,
) -> EstimateJourneyOutput:
    return EstimateJourneyOutput(
        estimate_id=f"unavailable:{origin_place_id}:{destination_place_id}",
        duration_seconds=0,
        provider="unavailable",
        observed_at=None,
        available=False,
        reason=reason,
    )


def _elapsed_ms(started: float) -> int:
    return max(int((time.monotonic() - started) * 1000), 0)
