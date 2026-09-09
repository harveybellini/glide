"""Server-side validation matrix for the deterministic tool host.

The Strands model may observe and propose, but every reference, arithmetic
result, and conflict check passes through ``ToolHost``. These tests pin the
rejection branches so a model cannot talk the executor into an unsafe plan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from glide.adapters.fixtures import (
    PLACES,
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    place_index,
)
from glide.agent.host import ToolHost
from glide.agent.tools import JourneyPair, PlannedJourney, ProposePlanInput
from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    PlanAction,
    Transparency,
    UserSettings,
)

from tests.unit.test_strands_runner import (
    canonical_proposal,
    estimate_pair,
    make_host,
)

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)


class FailingRouter:
    def estimate(self, **kwargs):
        raise RuntimeError("provider down")


def build_host(*, events=None, router=None, place_search=None, index=None, **kwargs):
    calendar = FixtureCalendar(day=DAY)
    events = events if events is not None else calendar.events()
    return ToolHost(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index=index if index is not None else place_index(events),
        router=router if router is not None else FixtureRouter(),
        now=NOW,
        place_search=place_search,
        **kwargs,
    )


def journey(pair, *, action="create", reason_code="feasible", **overrides):
    fields = {
        "journey_key": pair.journey_key,
        "origin_occurrence_id": pair.origin_occurrence_id,
        "destination_occurrence_id": pair.destination_occurrence_id,
        "action": action,
        "reason_code": reason_code,
    }
    fields.update(overrides)
    return PlannedJourney(**fields)


def single_event(**overrides) -> CalendarEvent:
    fields = {
        "provider_event_id": "evt_h",
        "occurrence_id": "occ_h",
        "calendar_id": "fixture-primary",
        "etag": "etag-h",
        "start": NOW + timedelta(hours=5),
        "end": NOW + timedelta(hours=6),
        "original_time_zone": "Europe/London",
        "title": "Design review",
        "location": "Hybrid Venue",
        "status": EventStatus.CONFIRMED,
        "transparency": Transparency.OPAQUE,
        "attendance": Attendance.ACCEPTED,
        "kind": EventKind.UNKNOWN,
    }
    fields.update(overrides)
    return CalendarEvent(**fields)


def test_pair_for_unknown_journey_raises() -> None:
    host = make_host()

    with pytest.raises(ValueError, match="unknown journey key"):
        host.pair_for("missing")


def test_read_schedule_inverted_window_returns_empty() -> None:
    host = make_host()

    output = host.read_schedule(
        run_id=host.run_id,
        window_start=NOW + timedelta(hours=2),
        window_end=NOW,
    )

    assert output.events == []
    assert output.busy_intervals == []


def test_lookup_place_delegates_to_provider_ephemerally() -> None:
    class Provider:
        def __init__(self):
            self.calls = []

        def search(self, *, query, region=None, storage_allowed=False):
            self.calls.append((query, region, storage_allowed))
            return [PLACES["b"]]

    provider = Provider()
    host = build_host(place_search=provider)

    output = host.lookup_place(query="clinic")

    assert [candidate.id for candidate in output.candidates] == ["place_b"]
    assert output.confirmed_alias is None
    assert provider.calls == [("clinic", None, False)]


def test_estimate_journey_rejects_unknown_place_refs() -> None:
    host = make_host()

    with pytest.raises(ValueError, match="unknown origin"):
        host.estimate_journey(
            origin_place_id="missing",
            destination_place_id="place_b",
            mode="driving",
            timing="arrive_by",
            timing_time=NOW + timedelta(hours=3),
        )
    with pytest.raises(ValueError, match="unknown destination"):
        host.estimate_journey(
            origin_place_id="place_a",
            destination_place_id="missing",
            mode="driving",
            timing="arrive_by",
            timing_time=NOW + timedelta(hours=3),
        )


def test_router_failure_becomes_typed_unavailable() -> None:
    host = build_host(router=FailingRouter())

    output = host.estimate_journey(
        origin_place_id="place_a",
        destination_place_id="place_b",
        mode="driving",
        timing="arrive_by",
        timing_time=NOW + timedelta(hours=3),
    )

    assert output.available is False
    assert output.reason == "no_route"
    assert ("place_a", "place_b") in host.unavailable_routes


def test_evaluate_candidate_rejects_unknown_estimate() -> None:
    host = make_host()

    with pytest.raises(ValueError, match="unknown route estimate"):
        host.evaluate_candidate(
            origin_available=NOW,
            destination_start=NOW + timedelta(hours=1),
            estimate_id="missing",
            padding_minutes=10,
        )


def test_request_decision_validates_occurrence_actions_and_dedupes() -> None:
    host = make_host()
    pair = host.shown_pairs[0]

    with pytest.raises(ValueError, match="does not match"):
        host.request_decision(
            journey_key=pair.journey_key,
            occurrence_id="wrong",
            reason_code="insufficient_time",
            facts={},
            allowed_actions=["skip_journey"],
        )
    with pytest.raises(ValueError, match="not permitted"):
        host.request_decision(
            journey_key=pair.journey_key,
            occurrence_id=pair.destination_occurrence_id,
            reason_code="insufficient_time",
            facts={},
            allowed_actions=["delete_everything"],
        )

    first = host.request_decision(
        journey_key=pair.journey_key,
        occurrence_id=pair.destination_occurrence_id,
        reason_code="insufficient_time",
        facts={"shortfall_seconds": 600},
        allowed_actions=["skip_journey"],
    )
    second = host.request_decision(
        journey_key=pair.journey_key,
        occurrence_id=pair.destination_occurrence_id,
        reason_code="insufficient_time",
        facts={},
        allowed_actions=["skip_journey"],
    )

    assert first.decision_id == second.decision_id
    assert host.decision_requests[pair.journey_key].allowed_actions == (
        "skip_journey",
    )


def test_accept_proposal_rejects_wrong_run_id_and_duplicate() -> None:
    host = make_host()
    proposal = canonical_proposal(host)
    assert host.accept_proposal(proposal).accepted is True

    duplicate = host.accept_proposal(proposal)
    assert duplicate.accepted is False
    assert "already been accepted" in duplicate.reason

    fresh = make_host()
    wrong = ProposePlanInput(
        run_id="other-run",
        journeys=canonical_proposal(fresh).journeys,
        summary="wrong run",
    )
    rejected = fresh.accept_proposal(wrong)
    assert rejected.accepted is False
    assert "server-bound" in rejected.reason


def test_accept_proposal_rejects_duplicate_missing_and_unknown_journeys() -> None:
    host = make_host()
    pair = host.shown_pairs[0]
    j = journey(pair)

    duplicate = host.accept_proposal(
        ProposePlanInput(run_id=host.run_id, journeys=[j, j], summary="dup")
    )
    assert "duplicate journey keys" in duplicate.reason

    missing = host.accept_proposal(
        ProposePlanInput(run_id=host.run_id, journeys=[j], summary="partial")
    )
    assert "missing journeys" in missing.reason

    unknown = PlannedJourney(
        journey_key="nope",
        origin_occurrence_id="a",
        destination_occurrence_id="b",
        action="create",
        reason_code="feasible",
    )
    mixed = host.accept_proposal(
        ProposePlanInput(run_id=host.run_id, journeys=[j, unknown], summary="mixed")
    )
    assert "unknown journeys" in mixed.reason


def test_create_journey_rejects_mismatched_pairs_and_wrong_reason() -> None:
    host = make_host()
    pair = host.shown_pairs[0]

    mismatched = PlannedJourney(
        journey_key=pair.journey_key,
        origin_occurrence_id="wrong",
        destination_occurrence_id=pair.destination_occurrence_id,
        action="create",
        reason_code="feasible",
    )
    assert "does not match" in host._validate_journey(mismatched, pair, False)

    wrong_reason = journey(pair, reason_code="same_place")
    assert "reason_code 'feasible'" in host._validate_journey(wrong_reason, pair, False)


def test_create_journey_requires_resolved_places() -> None:
    host = make_host()
    host.place_index = {}
    pair = JourneyPair(
        journey_key="k",
        origin_occurrence_id="occ_x",
        destination_occurrence_id="occ_y",
        origin_available=NOW,
        destination_start=NOW + timedelta(hours=1),
        destination_arrival_target=NOW + timedelta(hours=1),
    )

    problem = host._validate_journey(journey(pair), pair, False)

    assert "unresolved place" in problem


def test_create_journey_rejects_estimate_from_different_places() -> None:
    host = make_host()
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}
    estimate, _ = estimate_pair(host, pairs["occ_b"], ("place_a", "place_b"))

    problem = host._validate_journey(
        journey(pairs["occ_c"], route_estimate_id=estimate.estimate_id),
        pairs["occ_c"],
        False,
    )

    assert "places do not match" in problem


def test_create_journey_rejects_deterministic_infeasibility() -> None:
    host = make_host()
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}
    estimate, evaluation = estimate_pair(host, pairs["occ_c"], ("place_b", "place_c"))
    assert evaluation.feasible is False

    problem = host._validate_journey(
        journey(pairs["occ_c"], route_estimate_id=estimate.estimate_id),
        pairs["occ_c"],
        False,
    )

    assert "infeasible" in problem


def test_remove_journey_validation_rules() -> None:
    host = make_host()
    pair = host.shown_pairs[0]

    wrong_reason = journey(pair, action="remove", reason_code="feasible")
    assert "reason_code 'same_place'" in host._validate_journey(wrong_reason, pair, False)

    timed = journey(pair, action="remove", reason_code="same_place", route_estimate_id="x")
    assert "must not reference a timed route estimate" in host._validate_journey(timed, pair, False)

    different = journey(pair, action="remove", reason_code="same_place")
    assert "same place" in host._validate_journey(different, pair, False)

    unresolved = JourneyPair(
        journey_key="k",
        origin_occurrence_id="occ_x",
        destination_occurrence_id="occ_y",
        origin_available=NOW,
        destination_start=NOW + timedelta(hours=1),
        destination_arrival_target=NOW + timedelta(hours=1),
    )
    host.place_index = {}
    problem = host._validate_journey(
        journey(unresolved, action="remove", reason_code="same_place"),
        unresolved,
        False,
    )
    assert "both places to resolve" in problem

    self_pair = JourneyPair(
        journey_key="self",
        origin_occurrence_id="occ_z",
        destination_occurrence_id="occ_z",
        origin_available=NOW,
        destination_start=NOW + timedelta(hours=1),
        destination_arrival_target=NOW + timedelta(hours=1),
    )
    host.place_index = {"occ_z": PLACES["a"]}
    assert (
        host._validate_journey(
            journey(self_pair, action="remove", reason_code="same_place"),
            self_pair,
            False,
        )
        is None
    )


def test_decision_reason_code_validation_branches() -> None:
    host = make_host()
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}

    invalid = journey(pairs["occ_b"], action="decision", reason_code="nonsense")
    assert "not a model decision" in host._validate_journey(invalid, pairs["occ_b"], False)

    missing_estimate = journey(
        pairs["occ_b"], action="decision", reason_code="insufficient_time"
    )
    assert "requires a timed route estimate" in host._validate_journey(
        missing_estimate, pairs["occ_b"], False
    )

    feasible, _ = estimate_pair(host, pairs["occ_b"], ("place_a", "place_b"))
    feasible_conflict = journey(
        pairs["occ_b"],
        action="decision",
        reason_code="insufficient_time",
        route_estimate_id=feasible.estimate_id,
    )
    assert "emit a create journey instead" in host._validate_journey(
        feasible_conflict, pairs["occ_b"], False
    )

    wrong_estimate = journey(
        pairs["occ_c"],
        action="decision",
        reason_code="insufficient_time",
        route_estimate_id=feasible.estimate_id,
    )
    assert "places do not match" in host._validate_journey(
        wrong_estimate, pairs["occ_c"], False
    )

    resolved = journey(
        pairs["occ_b"], action="decision", reason_code="unknown_location"
    )
    assert "not justified" in host._validate_journey(resolved, pairs["occ_b"], False)

    unknown_start = journey(
        pairs["occ_b"], action="decision", reason_code="unknown_start"
    )
    assert "only valid when the start address is missing" in host._validate_journey(
        unknown_start, pairs["occ_b"], False
    )

    no_route = journey(pairs["occ_b"], action="decision", reason_code="no_route")
    assert "no route failure was observed" in host._validate_journey(
        no_route, pairs["occ_b"], False
    )

    hybrid = journey(pairs["occ_b"], action="decision", reason_code="hybrid_meeting")
    assert "self journey" in host._validate_journey(hybrid, pairs["occ_b"], False)

    all_day = journey(pairs["occ_b"], action="decision", reason_code="all_day")
    assert "self journey" in host._validate_journey(all_day, pairs["occ_b"], False)

    downstream = journey(
        pairs["occ_b"], action="decision", reason_code="downstream_uncertain"
    )
    assert "unresolved upstream" in host._validate_journey(
        downstream, pairs["occ_b"], False
    )

    forbidden = journey(pairs["occ_b"], action="update", reason_code="move")
    assert "not permitted" in host._validate_journey(forbidden, pairs["occ_b"], False)


def test_no_route_decision_is_accepted_after_observed_failure() -> None:
    host = build_host(router=FailingRouter())
    host.estimate_journey(
        origin_place_id="place_a",
        destination_place_id="place_b",
        mode="driving",
        timing="arrive_by",
        timing_time=NOW + timedelta(hours=3),
    )
    pair = host.shown_pairs[0]
    candidate = journey(pair, action="decision", reason_code="no_route")

    assert host._validate_journey(candidate, pair, False) is None
    facts = host._decision_facts(candidate, pair)
    assert facts["origin_place_id"] == "place_a"
    assert facts["destination_place_id"] == "place_b"


def test_hybrid_meeting_requires_uncertain_meeting_with_location() -> None:
    physical = build_host(
        events=[single_event(all_day=True, kind=EventKind.PHYSICAL)],
        index={"occ_h": PLACES["a"]},
    )
    pair = physical.shown_pairs[0]
    assert "uncertain meeting" in physical._validate_journey(
        journey(pair, action="decision", reason_code="hybrid_meeting"), pair, False
    )

    no_location = build_host(events=[single_event(all_day=True, location=None)])
    pair = no_location.shown_pairs[0]
    assert "uncertain meeting" in no_location._validate_journey(
        journey(pair, action="decision", reason_code="hybrid_meeting"), pair, False
    )


def test_all_day_requires_opaque_all_day_event() -> None:
    host = build_host(events=[single_event()])
    pair = host.shown_pairs[0]

    problem = host._validate_journey(
        journey(pair, action="decision", reason_code="all_day"), pair, False
    )

    assert "requires an opaque all-day event" in problem


def test_evidence_must_be_scalar() -> None:
    host = make_host()
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}
    estimate, _ = estimate_pair(host, pairs["occ_b"], ("place_a", "place_b"))
    forged = PlannedJourney.model_construct(
        journey_key=pairs["occ_b"].journey_key,
        origin_occurrence_id=pairs["occ_b"].origin_occurrence_id,
        destination_occurrence_id=pairs["occ_b"].destination_occurrence_id,
        action="create",
        reason_code="feasible",
        route_estimate_id=estimate.estimate_id,
        evidence={"bad": [1, 2]},
    )

    problem = host._validate_journey(forged, pairs["occ_b"], False)

    assert "must be an int, string, or boolean" in problem


def test_decision_facts_fill_default_and_all_day() -> None:
    host = make_host()
    pair = host.shown_pairs[0]

    default = journey(pair, action="decision", reason_code="unknown_start")
    assert host._decision_facts(default, pair) == {}

    all_day = journey(pair, action="decision", reason_code="all_day")
    assert host._decision_facts(all_day, pair) == {
        "occurrence_id": pair.destination_occurrence_id
    }


def test_recorded_tool_logs_invalid_input_as_error() -> None:
    host = make_host()

    def failing_operation():
        raise ValueError("invalid input")

    with pytest.raises(ValueError, match="invalid input"):
        host._record("read_schedule", failing_operation, "schedule_read")

    records = [record for record in host.tool_log if record.name == "read_schedule"]
    assert len(records) == 1
    assert records[0].outcome == "error"
    assert records[0].reason_code == "invalid_input"


def test_materialize_requires_accepted_proposal() -> None:
    host = make_host()

    with pytest.raises(ValueError, match="no accepted proposal"):
        host.materialize_plans()


def test_materialize_remove_for_self_journey() -> None:
    host = build_host(
        events=[single_event()],
        index={"occ_h": PLACES["a"]},
    )
    pair = host.shown_pairs[0]
    assert pair.origin_occurrence_id == pair.destination_occurrence_id

    accepted = host.accept_proposal(
        ProposePlanInput(
            run_id=host.run_id,
            journeys=[journey(pair, action="remove", reason_code="same_place")],
            summary="same place",
        )
    )
    assert accepted.accepted is True

    plans = host.materialize_plans()
    assert len(plans) == 1
    assert plans[0].action == PlanAction.REMOVE
    assert plans[0].route_estimate_id == "same-place"


def test_materialize_merges_decision_request_facts() -> None:
    host = make_host()
    pair_c = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}["occ_c"]
    estimate, _ = estimate_pair(host, pair_c, ("place_b", "place_c"))
    host.request_decision(
        journey_key=pair_c.journey_key,
        occurrence_id=pair_c.destination_occurrence_id,
        reason_code="insufficient_time",
        facts={"note": "custom"},
        allowed_actions=["skip_journey"],
    )

    proposal = canonical_proposal(host)
    assert host.accept_proposal(proposal).accepted is True
    plans = host.materialize_plans()
    plan = next(plan for plan in plans if plan.journey_key == pair_c.journey_key)

    assert plan.reason_code == "insufficient_time"
    assert plan.calculated_facts["note"] == "custom"
    assert plan.calculated_facts["shortfall_seconds"] == 10 * 60
