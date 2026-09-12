from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    place_index,
)
from glide.agent.host import ToolHost
from glide.agent.strands_runner import (
    AgentDeadlineExceeded,
    AgentProposalMissing,
    InvocationResult,
    StrandsAgentRunner,
    build_tools,
)
from glide.agent.tools import PlannedJourney, ProposePlanInput
from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    PlaceRef,
    PlanAction,
    StoragePolicyStatus,
    Transparency,
    UserSettings,
)
from glide.domain.scheduling import build_journey_plans

DAY = datetime(2026, 9, 9, tzinfo=UTC).date()
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)


def make_host(
    events: list[CalendarEvent] | None = None,
    *,
    max_events: int = 20,
    max_journeys: int = 10,
    max_routes: int = 30,
) -> ToolHost:
    calendar = FixtureCalendar(day=DAY)
    events = events if events is not None else calendar.events()
    settings = UserSettings.model_validate(canonical_settings())
    return ToolHost(
        settings=settings,
        events=events,
        place_index=place_index(events),
        router=FixtureRouter(),
        now=NOW,
        max_events=max_events,
        max_journeys=max_journeys,
        max_routes=max_routes,
    )


def estimate_pair(host: ToolHost, pair, place_ids: tuple[str, str]):
    estimate = host.estimate_journey(
        origin_place_id=place_ids[0],
        destination_place_id=place_ids[1],
        mode="driving",
        timing="arrive_by",
        timing_time=pair.destination_arrival_target,
    )
    assert estimate.available is True
    evaluation = host.evaluate_candidate(
        origin_available=pair.origin_available,
        destination_start=pair.destination_start,
        estimate_id=estimate.estimate_id,
        padding_minutes=10,
    )
    return estimate, evaluation


def canonical_proposal(host: ToolHost) -> ProposePlanInput:
    journeys: list[PlannedJourney] = []
    for pair in host.shown_pairs:
        origin = host.place_for(pair.origin_occurrence_id)
        destination = host.place_for(pair.destination_occurrence_id)
        assert origin is not None and destination is not None
        estimate, evaluation = estimate_pair(host, pair, (origin.id, destination.id))
        if evaluation.feasible:
            journeys.append(
                PlannedJourney(
                    journey_key=pair.journey_key,
                    origin_occurrence_id=pair.origin_occurrence_id,
                    destination_occurrence_id=pair.destination_occurrence_id,
                    action="create",
                    reason_code="feasible",
                    route_estimate_id=estimate.estimate_id,
                    evidence={"duration_seconds": estimate.duration_seconds},
                )
            )
        else:
            journeys.append(
                PlannedJourney(
                    journey_key=pair.journey_key,
                    origin_occurrence_id=pair.origin_occurrence_id,
                    destination_occurrence_id=pair.destination_occurrence_id,
                    action="decision",
                    reason_code="insufficient_time",
                    route_estimate_id=estimate.estimate_id,
                    evidence={"shortfall_seconds": evaluation.shortfall_seconds},
                )
            )
    return ProposePlanInput(run_id=host.run_id, journeys=journeys, summary="canonical day")


def scripted_factory(scripts):
    """Return an agent factory that replays one script per invocation."""

    calls: list[str] = []

    def factory(tools, system_prompt, host):
        def invoke(prompt, *, limits=None, cancel_signal=None):
            calls.append(prompt)
            script = scripts[min(len(calls) - 1, len(scripts) - 1)]
            script(host)
            return InvocationResult(stop_reason="end_turn")

        return invoke

    return factory, calls


def run_with_scripts(scripts, **runner_kwargs):
    factory, calls = scripted_factory(scripts)
    runner = StrandsAgentRunner(agent_factory=factory, **runner_kwargs)
    calendar = FixtureCalendar(day=DAY)
    events = calendar.events()
    plans = runner.run(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index=place_index(events),
        router=FixtureRouter(),
        now=NOW,
    )
    return plans, calls


def test_tool_schemas_expose_six_plan_tools() -> None:
    tools = build_tools(make_host())
    assert [tool.tool_name for tool in tools] == [
        "read_schedule",
        "lookup_place",
        "estimate_journey",
        "evaluate_candidate",
        "request_decision",
        "propose_plan",
    ]
    schedule_schema = tools[0].tool_spec["inputSchema"]["json"]
    assert schedule_schema["required"] == ["run_id", "window_start", "window_end"]
    propose_schema = tools[5].tool_spec["inputSchema"]["json"]
    assert propose_schema["required"] == ["run_id", "journeys", "summary"]


def test_read_schedule_is_server_bound_and_window_clamped() -> None:
    host = make_host()
    output = host.read_schedule(
        run_id=host.run_id,
        window_start=host.window_start,
        window_end=host.window_end,
    )
    assert len(output.events) == 3
    assert [pair.destination_occurrence_id for pair in output.journey_pairs] == [
        "occ_b",
        "occ_c",
    ]
    assert output.scope_exceeded is False

    with pytest.raises(ValueError, match="server-bound"):
        host.read_schedule(
            run_id="other-run",
            window_start=host.window_start,
            window_end=host.window_end,
        )

    narrow = host.read_schedule(
        run_id=host.run_id,
        window_start=NOW + timedelta(hours=4),
        window_end=NOW + timedelta(hours=6),
    )
    assert [event.occurrence_id for event in narrow.events] == ["occ_b", "occ_c"]


def test_lookup_place_confirms_alias_and_rejects_unknown_text() -> None:
    host = make_host()
    confirmed = host.lookup_place(query="Westfield Surgery")
    assert confirmed.confirmed_alias is not None
    assert confirmed.confirmed_alias.id == "place_b"
    assert confirmed.confirmed_alias.confirmed is True

    missing = host.lookup_place(query="Narnia")
    assert missing.candidates == []
    assert missing.confirmed_alias is None
    assert "no matching place" in (missing.reason or "")


def test_lookup_place_reports_ambiguity_without_confirming() -> None:
    host = make_host()
    north = PlaceRef(
        id="hall-north",
        label="City Hall North",
        provenance="synthetic fixture",
        confirmed=True,
        storage_policy_status=StoragePolicyStatus.EPHEMERAL,
    )
    south = PlaceRef(
        id="hall-south",
        label="City Hall South",
        provenance="synthetic fixture",
        confirmed=True,
        storage_policy_status=StoragePolicyStatus.EPHEMERAL,
    )
    host.place_index = {"occ_x": north, "occ_y": south}

    output = host.lookup_place(query="City Hall")

    assert output.confirmed_alias is None
    assert {candidate.id for candidate in output.candidates} == {
        "hall-north",
        "hall-south",
    }


def test_estimate_and_evaluate_match_deterministic_arithmetic() -> None:
    host = make_host()
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}

    estimate, evaluation = estimate_pair(
        host,
        pairs["occ_b"],
        ("place_a", "place_b"),
    )
    assert estimate.duration_seconds == 25 * 60
    assert evaluation.feasible is True
    assert evaluation.proposed_start == pairs["occ_b"].destination_start - timedelta(
        minutes=35
    )
    assert evaluation.proposed_end == pairs["occ_b"].destination_start

    _, conflict = estimate_pair(
        host,
        pairs["occ_c"],
        ("place_b", "place_c"),
    )
    assert conflict.feasible is False
    assert conflict.reason_code == "insufficient_time"
    assert conflict.shortfall_seconds == 10 * 60


def test_route_budget_returns_typed_unavailable() -> None:
    host = make_host(max_routes=1)
    pairs = {pair.destination_occurrence_id: pair for pair in host.shown_pairs}
    first = host.estimate_journey(
        origin_place_id="place_a",
        destination_place_id="place_b",
        mode="driving",
        timing="arrive_by",
        timing_time=pairs["occ_b"].destination_arrival_target,
    )
    assert first.available is True
    second = host.estimate_journey(
        origin_place_id="place_b",
        destination_place_id="place_c",
        mode="driving",
        timing="arrive_by",
        timing_time=pairs["occ_c"].destination_arrival_target,
    )
    assert second.available is False
    assert second.reason == "route_budget_exceeded"
    assert host.route_calls == 1


def test_accept_proposal_rekeys_a_journey_identified_by_occurrences() -> None:
    """An invented journey key is mapped back to the pair it names.

    The deployed model submits ``start_place -> occ_b``-style entries with its
    own key; the server's pair is unambiguous, so the entry is re-keyed instead
    of failing the run.
    """

    host = make_host()
    proposal = canonical_proposal(host)
    first, *rest = proposal.journeys
    rekeyed = [first.model_copy(update={"journey_key": "invented-key"}), *rest]

    output = host.accept_proposal(
        ProposePlanInput(run_id=host.run_id, journeys=rekeyed, summary="bad")
    )

    assert output.accepted is True
    assert {item.journey_key for item in output.journeys} == {
        pair.journey_key for pair in host.shown_pairs
    }


def test_accept_proposal_requires_evidence_backed_create() -> None:
    host = make_host()
    pair = host.shown_pairs[0]
    proposal = ProposePlanInput(
        run_id=host.run_id,
        journeys=[
            PlannedJourney(
                journey_key=pair.journey_key,
                origin_occurrence_id=pair.origin_occurrence_id,
                destination_occurrence_id=pair.destination_occurrence_id,
                action="create",
                reason_code="feasible",
                route_estimate_id="never-estimated",
            ),
            PlannedJourney(
                journey_key=host.shown_pairs[1].journey_key,
                origin_occurrence_id=host.shown_pairs[1].origin_occurrence_id,
                destination_occurrence_id=host.shown_pairs[1].destination_occurrence_id,
                action="decision",
                reason_code="insufficient_time",
            ),
        ],
        summary="not evidence backed",
    )
    output = host.accept_proposal(proposal)
    assert output.accepted is False
    assert "route estimate" in (output.reason or "")


def test_materialize_matches_deterministic_planner() -> None:
    host = make_host()
    accepted = host.accept_proposal(canonical_proposal(host))
    assert accepted.accepted is True
    plans = host.materialize_plans()

    deterministic = build_journey_plans(
        settings=UserSettings.model_validate(canonical_settings()),
        events=host.events,
        place_index=host.place_index,
        estimator=FixtureRouter(),
        now=NOW,
    )
    assert [plan.journey_key for plan in plans] == [
        plan.journey_key for plan in deterministic
    ]
    assert [plan.reason_code for plan in plans] == [
        plan.reason_code for plan in deterministic
    ]
    create = next(plan for plan in plans if plan.action == PlanAction.CREATE)
    decision = next(plan for plan in plans if plan.action == PlanAction.DECISION)
    assert create.destination_occurrence_id == "occ_b"
    assert create.proposed_start == deterministic[0].proposed_start
    assert create.proposed_end == deterministic[0].proposed_end
    assert decision.reason_code == "insufficient_time"
    assert decision.calculated_facts["shortfall_seconds"] == 600


def test_runner_scripts_full_flow_once() -> None:
    def script(host: ToolHost) -> None:
        host.accept_proposal(canonical_proposal(host))

    plans, calls = run_with_scripts([script])
    assert len(plans) == 2
    assert len(calls) == 1
    assert {plan.reason_code for plan in plans} == {"feasible", "insufficient_time"}


def test_runner_repairs_a_rejected_proposal_exactly_once() -> None:
    attempts = {"count": 0}

    def first(host: ToolHost) -> None:
        attempts["count"] += 1
        host.accept_proposal(
            ProposePlanInput(
                run_id=host.run_id,
                journeys=[
                    PlannedJourney(
                        journey_key="invented",
                        origin_occurrence_id="start_place",
                        destination_occurrence_id="occ_b",
                        action="create",
                        reason_code="feasible",
                    )
                ],
                summary="bad",
            )
        )

    def second(host: ToolHost) -> None:
        attempts["count"] += 1
        host.accept_proposal(canonical_proposal(host))

    plans, calls = run_with_scripts([first, second])
    assert len(plans) == 2
    assert attempts["count"] == 2
    assert len(calls) == 2
    assert "not accepted" in calls[1]


def test_runner_raises_safe_failure_when_no_proposal_is_accepted() -> None:
    def script(host: ToolHost) -> None:
        host.read_schedule(
            run_id=host.run_id,
            window_start=host.window_start,
            window_end=host.window_end,
        )

    factory, calls = scripted_factory([script, script])
    runner = StrandsAgentRunner(agent_factory=factory)
    calendar = FixtureCalendar(day=DAY)
    events = calendar.events()
    with pytest.raises(AgentProposalMissing):
        runner.run(
            settings=UserSettings.model_validate(canonical_settings()),
            events=events,
            place_index=place_index(events),
            router=FixtureRouter(),
            now=NOW,
        )
    assert len(calls) == 2


def test_runner_enforces_application_deadline() -> None:
    def factory(tools, system_prompt, host):
        def invoke(prompt, *, limits=None, cancel_signal=None):
            assert cancel_signal is not None
            cancel_signal.wait(timeout=5)
            return InvocationResult(stop_reason="cancelled")

        return invoke

    runner = StrandsAgentRunner(agent_factory=factory, deadline_seconds=0.05)
    calendar = FixtureCalendar(day=DAY)
    events = calendar.events()
    with pytest.raises(AgentDeadlineExceeded):
        runner.run(
            settings=UserSettings.model_validate(canonical_settings()),
            events=events,
            place_index=place_index(events),
            router=FixtureRouter(),
            now=NOW,
        )


def test_scope_exceeded_produces_explicit_decisions() -> None:
    """Above the journey cap the host still emits an explicit decision.

    The events carry resolved locations so their pairs stay decidable: a pair
    with an unresolved location is materialized as a decision by the host
    before the model ever sees it, which is a different branch.
    """

    from glide.adapters.fixtures import PLACES

    events = [
        CalendarEvent(
            provider_event_id=f"evt_{index}",
            occurrence_id=f"occ_{index}",
            calendar_id="fixture-primary",
            etag=f"etag-{index}",
            start=NOW + timedelta(hours=index * 2 + 2),
            end=NOW + timedelta(hours=index * 2 + 2, minutes=30),
            original_time_zone="Europe/London",
            title=f"Event {index}",
            location=(
                "Northside Community Centre" if index % 2 == 0 else "Westfield Surgery"
            ),
            status=EventStatus.CONFIRMED,
            transparency=Transparency.OPAQUE,
            attendance=Attendance.ACCEPTED,
            kind=EventKind.PHYSICAL,
        )
        for index in range(12)
    ]
    host = ToolHost(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index={
            f"occ_{index}": PLACES["a" if index % 2 == 0 else "b"]
            for index in range(12)
        },
        router=FixtureRouter(),
        now=NOW,
        max_events=3,
        max_journeys=2,
    )
    schedule = host.read_schedule(
        run_id=host.run_id,
        window_start=host.window_start,
        window_end=host.window_end,
    )
    assert schedule.scope_exceeded is True
    assert len(schedule.events) == 3
    assert len(schedule.journey_pairs) == 2

    journeys: list[PlannedJourney] = []
    for pair in host.shown_pairs:
        origin = host.place_for(pair.origin_occurrence_id)
        destination = host.place_for(pair.destination_occurrence_id)
        assert origin is not None and destination is not None
        estimate, evaluation = estimate_pair(host, pair, (origin.id, destination.id))
        assert evaluation.feasible is True
        journeys.append(
            PlannedJourney(
                journey_key=pair.journey_key,
                origin_occurrence_id=pair.origin_occurrence_id,
                destination_occurrence_id=pair.destination_occurrence_id,
                action="create",
                reason_code="feasible",
                route_estimate_id=estimate.estimate_id,
            )
        )
    accepted = host.accept_proposal(
        ProposePlanInput(run_id=host.run_id, journeys=journeys, summary="too many")
    )
    assert accepted.accepted is True
    plans = host.materialize_plans()
    assert len(plans) == len(host.all_pairs)
    assert plans[0].reason_code == "feasible"
    assert plans[2].reason_code == "scope_exceeded"
    assert plans[2].action == PlanAction.DECISION


def test_hybrid_meeting_decision_is_accepted_and_materialized() -> None:
    hybrid = CalendarEvent(
        provider_event_id="evt_h",
        occurrence_id="occ_h",
        calendar_id="fixture-primary",
        etag="etag-h",
        start=NOW + timedelta(hours=5),
        end=NOW + timedelta(hours=6),
        original_time_zone="Europe/London",
        title="Design review",
        location="Hybrid Venue",
        status=EventStatus.CONFIRMED,
        transparency=Transparency.OPAQUE,
        attendance=Attendance.ACCEPTED,
        kind=EventKind.UNKNOWN,
    )
    host = ToolHost(
        settings=UserSettings.model_validate(canonical_settings()),
        events=[hybrid],
        place_index={},
        router=FixtureRouter(),
        now=NOW,
    )
    pair = host.shown_pairs[0]
    assert pair.origin_occurrence_id == pair.destination_occurrence_id == "occ_h"

    accepted = host.accept_proposal(
        ProposePlanInput(
            run_id=host.run_id,
            journeys=[
                PlannedJourney(
                    journey_key=pair.journey_key,
                    origin_occurrence_id=pair.origin_occurrence_id,
                    destination_occurrence_id=pair.destination_occurrence_id,
                    action="decision",
                    reason_code="hybrid_meeting",
                )
            ],
            summary="uncertain meeting",
        )
    )
    assert accepted.accepted is True

    plans = host.materialize_plans()
    assert len(plans) == 1
    assert plans[0].action == PlanAction.DECISION
    assert plans[0].reason_code == "hybrid_meeting"
    assert plans[0].calculated_facts["occurrence_id"] == "occ_h"


def test_downstream_uncertain_requires_an_unresolved_upstream() -> None:
    """A downstream journey may only claim ``downstream_uncertain`` when an
    earlier journey in the same proposal was left unresolved.

    The upstream trigger is a model-raised shortfall: the pair's places resolve,
    but the drive does not fit. A pair with an unresolved location never reaches
    the model, because the host materializes that decision itself.
    """

    from glide.adapters.fixtures import PLACES

    letters = "abc"
    locations = [
        "Northside Community Centre",
        "Westfield Surgery",
        "Oakfield Primary School",
    ]
    index = {"occ_a": PLACES["a"], "occ_b": PLACES["b"], "occ_c": PLACES["c"]}

    def build(second_hour: int, third_hour: int) -> ToolHost:
        events = []
        offsets = (3, second_hour, third_hour)
        for letter, location, offset in zip(letters, locations, offsets, strict=True):
            start = NOW + timedelta(hours=offset)
            events.append(
                CalendarEvent(
                    provider_event_id=f"evt_{letter}",
                    occurrence_id=f"occ_{letter}",
                    calendar_id="fixture-primary",
                    etag=f"etag-{letter}",
                    start=start,
                    end=start + timedelta(minutes=30),
                    original_time_zone="Europe/London",
                    title=f"Event {letter}",
                    location=location,
                    status=EventStatus.CONFIRMED,
                    transparency=Transparency.OPAQUE,
                    attendance=Attendance.ACCEPTED,
                    kind=EventKind.PHYSICAL,
                )
            )
        return ToolHost(
            settings=UserSettings.model_validate(canonical_settings()),
            events=events,
            place_index=index,
            router=FixtureRouter(),
            now=NOW,
        )

    def first_pair_estimate(host: ToolHost):
        pair = host.shown_pairs[0]
        origin = host.place_for(pair.origin_occurrence_id)
        destination = host.place_for(pair.destination_occurrence_id)
        assert origin is not None and destination is not None
        return pair, estimate_pair(host, pair, (origin.id, destination.id))

    # occ_a 09:00-09:30, occ_b 10:00-10:30, occ_c 12:00-12:30: the drive from
    # occ_a to occ_b needs 35 minutes and only 30 are free.
    host = build(second_hour=4, third_hour=6)
    pairs = host.shown_pairs
    assert [pair.destination_occurrence_id for pair in pairs] == ["occ_b", "occ_c"]
    first, (estimate, evaluation) = first_pair_estimate(host)
    assert evaluation.feasible is False

    accepted = host.accept_proposal(
        ProposePlanInput(
            run_id=host.run_id,
            journeys=[
                PlannedJourney(
                    journey_key=first.journey_key,
                    origin_occurrence_id=first.origin_occurrence_id,
                    destination_occurrence_id=first.destination_occurrence_id,
                    action="decision",
                    reason_code="insufficient_time",
                    route_estimate_id=estimate.estimate_id,
                ),
                PlannedJourney(
                    journey_key=pairs[1].journey_key,
                    origin_occurrence_id=pairs[1].origin_occurrence_id,
                    destination_occurrence_id=pairs[1].destination_occurrence_id,
                    action="decision",
                    reason_code="downstream_uncertain",
                ),
            ],
            summary="shortfall chain",
        )
    )
    assert accepted.accepted is True
    assert [plan.reason_code for plan in host.materialize_plans()] == [
        "insufficient_time",
        "downstream_uncertain",
    ]

    # With room for both drives the same downstream claim has no unresolved
    # upstream to lean on, so it is rejected.
    fresh = build(second_hour=6, third_hour=12)
    fresh_pairs = fresh.shown_pairs
    assert [pair.destination_occurrence_id for pair in fresh_pairs] == [
        "occ_b",
        "occ_c",
    ]
    first, (estimate, evaluation) = first_pair_estimate(fresh)
    assert evaluation.feasible is True

    bad = fresh.accept_proposal(
        ProposePlanInput(
            run_id=fresh.run_id,
            journeys=[
                PlannedJourney(
                    journey_key=first.journey_key,
                    origin_occurrence_id=first.origin_occurrence_id,
                    destination_occurrence_id=first.destination_occurrence_id,
                    action="create",
                    reason_code="feasible",
                    route_estimate_id=estimate.estimate_id,
                ),
                PlannedJourney(
                    journey_key=fresh_pairs[1].journey_key,
                    origin_occurrence_id=fresh_pairs[1].origin_occurrence_id,
                    destination_occurrence_id=fresh_pairs[1].destination_occurrence_id,
                    action="decision",
                    reason_code="downstream_uncertain",
                ),
            ],
            summary="bad chain",
        )
    )
    assert bad.accepted is False
    assert "upstream" in (bad.reason or "")


def test_build_agent_runner_falls_back_when_bedrock_is_unconfigured(
    monkeypatch,
) -> None:
    from glide.agent.runner import DeterministicAgentRunner
    from glide.agent.strands_runner import build_agent_runner

    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setenv("GLIDE_AGENT_MODE", "bedrock")
    assert isinstance(build_agent_runner(), DeterministicAgentRunner)

    monkeypatch.setenv("GLIDE_AGENT_MODE", "deterministic")
    assert isinstance(build_agent_runner(), DeterministicAgentRunner)


def test_full_strands_loop_with_fake_model() -> None:
    """Drive the real Strands loop through a fake Bedrock-shaped model.

    This exercises the actual SDK tool executor, input schemas, and the
    stop-after-proposal hook rather than the scripted-invoker seam.
    """

    from strands.models.model import Model

    class FakeBedrockModel(Model):
        def __init__(self) -> None:
            self._runs: dict[str, dict] = {}
            self.stream_calls = 0

        def update_config(self, **model_config) -> None:
            pass

        def get_config(self) -> dict:
            return {}

        async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
            raise NotImplementedError
            yield  # pragma: no cover

        async def stream(
            self,
            messages,
            tool_specs=None,
            system_prompt=None,
            *,
            tool_choice=None,
            system_prompt_content=None,
            invocation_state=None,
            cancel_signal=None,
            **kwargs,
        ):
            self.stream_calls += 1
            tool_uses = self._next_tool_uses(messages)
            yield {"messageStart": {"role": "assistant"}}
            for index, use in enumerate(tool_uses):
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {
                                "toolUseId": f"fake-{self.stream_calls}-{index}",
                                "name": use["name"],
                            }
                        }
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "delta": {"toolUse": {"input": _json_dumps(use["input"])}}
                    }
                }
                yield {"contentBlockStop": {}}
            yield {
                "messageStop": {
                    "stopReason": "tool_use" if tool_uses else "end_turn"
                }
            }
            yield {
                "metadata": {
                    "usage": {"inputTokens": 1, "outputTokens": 1},
                    "metrics": {"latencyMs": 1},
                }
            }

        def _next_tool_uses(self, messages) -> list[dict]:
            prompt = _first_prompt(messages)
            run_id = _prompt_field(prompt, "Run:")
            state = self._runs.setdefault(
                run_id,
                {"schedule": None, "lookups": [], "estimates": [], "evaluations": []},
            )
            results = _tool_results(messages)

            for result in results:
                if "journey_pairs" in result and state["schedule"] is None:
                    state["schedule"] = result
                elif "candidates" in result:
                    state["lookups"].append(result)
                elif "estimate_id" in result and "duration_seconds" in result:
                    state["estimates"].append(result)
                elif "feasible" in result:
                    state["evaluations"].append(result)

            schedule = state["schedule"]
            pairs = schedule["journey_pairs"] if schedule else []
            if schedule is None:
                return [
                    {
                        "name": "read_schedule",
                        "input": {
                            "run_id": run_id,
                            "window_start": _prompt_field(prompt, "Planning window:")[0],
                            "window_end": _prompt_field(prompt, "Planning window:")[1],
                        },
                    }
                ]

            locations = _pair_locations(pairs)
            if len(state["lookups"]) < len(locations):
                return [
                    {"name": "lookup_place", "input": {"query": location}}
                    for location in locations
                ]

            place_by_label = _place_by_label(state["lookups"])
            if len(state["estimates"]) < len(pairs):
                return [
                    {
                        "name": "estimate_journey",
                        "input": {
                            "origin_place_id": place_by_label[pair["origin_location"]],
                            "destination_place_id": place_by_label[
                                pair["destination_location"]
                            ],
                            "mode": "driving",
                            "timing": "arrive_by",
                            "timing_time": pair["destination_arrival_target"],
                        },
                    }
                    for pair in pairs
                ]

            if len(state["evaluations"]) < len(pairs):
                return [
                    {
                        "name": "evaluate_candidate",
                        "input": {
                            "origin_available": pair["origin_available"],
                            "destination_start": pair["destination_start"],
                            "estimate_id": state["estimates"][index]["estimate_id"],
                            "padding_minutes": 10,
                        },
                    }
                    for index, pair in enumerate(pairs)
                ]

            accepted = any("accepted" in result and result["accepted"] for result in results)
            if not accepted:
                journeys = []
                for index, pair in enumerate(pairs):
                    evaluation = state["evaluations"][index]
                    estimate = state["estimates"][index]
                    if evaluation["feasible"]:
                        journeys.append(
                            {
                                "journey_key": pair["journey_key"],
                                "origin_occurrence_id": pair["origin_occurrence_id"],
                                "destination_occurrence_id": pair[
                                    "destination_occurrence_id"
                                ],
                                "action": "create",
                                "reason_code": "feasible",
                                "route_estimate_id": estimate["estimate_id"],
                                "evidence": {
                                    "duration_seconds": estimate["duration_seconds"]
                                },
                            }
                        )
                    else:
                        journeys.append(
                            {
                                "journey_key": pair["journey_key"],
                                "origin_occurrence_id": pair["origin_occurrence_id"],
                                "destination_occurrence_id": pair[
                                    "destination_occurrence_id"
                                ],
                                "action": "decision",
                                "reason_code": "insufficient_time",
                                "route_estimate_id": estimate["estimate_id"],
                                "evidence": {
                                    "shortfall_seconds": evaluation["shortfall_seconds"]
                                },
                            }
                        )
                return [
                    {
                        "name": "propose_plan",
                        "input": {
                            "run_id": run_id,
                            "journeys": journeys,
                            "summary": "canonical day",
                        },
                    }
                ]
            return []

    model = FakeBedrockModel()
    runner = StrandsAgentRunner(model=model)
    calendar = FixtureCalendar(day=DAY)
    events = calendar.events()
    plans = runner.run(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index=place_index(events),
        router=FixtureRouter(),
        now=NOW,
    )

    assert {plan.reason_code for plan in plans} == {"feasible", "insufficient_time"}
    create = next(plan for plan in plans if plan.action == PlanAction.CREATE)
    assert create.destination_occurrence_id == "occ_b"
    assert create.proposed_start == datetime(2026, 9, 9, 9, 25, tzinfo=UTC)
    assert create.proposed_end == datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    decision = next(plan for plan in plans if plan.action == PlanAction.DECISION)
    assert decision.calculated_facts["shortfall_seconds"] == 600
    # read_schedule, three lookups, two estimates, two evaluations, proposal:
    assert model.stream_calls == 5


def _first_prompt(messages) -> str:
    for message in messages:
        for block in message.get("content", []):
            if isinstance(block, dict) and "text" in block and "Run: " in block["text"]:
                return block["text"]
    raise AssertionError("no user prompt found in messages")


def _prompt_field(prompt: str, field: str) -> str | list[str]:
    line = next(line for line in prompt.splitlines() if line.startswith(field))
    value = line.split(field, 1)[1].strip()
    return value.split(" to ") if field == "Planning window:" else value


def _tool_results(messages) -> list[dict]:
    import json

    results: list[dict] = []
    for message in messages:
        for block in message.get("content", []):
            if not isinstance(block, dict) or "toolResult" not in block:
                continue
            for item in block["toolResult"].get("content", []):
                if isinstance(item, dict) and "text" in item:
                    results.append(json.loads(item["text"]))
    return results


def _pair_locations(pairs) -> list[str]:
    locations: list[str] = []
    for pair in pairs:
        for key in ("origin_location", "destination_location"):
            location = pair.get(key)
            if location and location not in locations:
                locations.append(location)
    return locations


def _place_by_label(lookups) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for lookup in lookups:
        alias = lookup.get("confirmed_alias")
        candidates = lookup.get("candidates", [])
        if alias:
            mapping[alias["label"]] = alias["id"]
        elif candidates:
            mapping[candidates[0]["label"]] = candidates[0]["id"]
    return mapping


def _json_dumps(value) -> str:
    import json

    return json.dumps(value, default=str)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"deadline_seconds": 0},
        {"max_events": 0},
        {"max_journeys": 0},
        {"max_routes": 0},
    ],
)
def test_runner_rejects_nonpositive_budgets(kwargs) -> None:
    with pytest.raises(ValueError):
        StrandsAgentRunner(**kwargs)


def test_build_agent_runner_fails_fast_in_production_without_model(
    monkeypatch,
) -> None:
    from glide.agent.strands_runner import AgentInvocationError, build_agent_runner

    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.setenv("GLIDE_AGENT_MODE", "bedrock")
    monkeypatch.setenv("GLIDE_ENV", "production")

    with pytest.raises(AgentInvocationError, match="could not be built"):
        build_agent_runner()


def test_build_agent_runner_selects_strands_runner_when_configured(
    monkeypatch,
) -> None:
    from glide.agent.strands_runner import build_agent_runner

    monkeypatch.setenv("GLIDE_AGENT_MODE", "strands")
    monkeypatch.setattr(
        "glide.agent.strands_runner.default_bedrock_model",
        lambda: object(),
    )

    assert isinstance(build_agent_runner(), StrandsAgentRunner)


def test_build_agent_runner_reads_deadline_and_turn_budget(monkeypatch) -> None:
    """The deployed worker tunes the agent budget through the environment."""

    from glide.agent.strands_runner import build_agent_runner

    monkeypatch.setattr(
        "glide.agent.strands_runner.default_bedrock_model",
        lambda: object(),
    )
    runner = build_agent_runner(
        {
            "GLIDE_AGENT_MODE": "bedrock",
            "GLIDE_AGENT_DEADLINE_SECONDS": "200",
            "GLIDE_AGENT_TURNS": "16",
        }
    )

    assert isinstance(runner, StrandsAgentRunner)
    assert runner.deadline_seconds == 200.0
    assert runner.limits["turns"] == 16


def test_build_agent_runner_ignores_invalid_budget_values(monkeypatch) -> None:
    from glide.agent.strands_runner import build_agent_runner

    monkeypatch.setattr(
        "glide.agent.strands_runner.default_bedrock_model",
        lambda: object(),
    )
    runner = build_agent_runner(
        {
            "GLIDE_AGENT_MODE": "bedrock",
            "GLIDE_AGENT_DEADLINE_SECONDS": "soon",
            "GLIDE_AGENT_TURNS": "many",
        }
    )

    from glide.agent.strands_runner import (
        DEFAULT_DEADLINE_SECONDS,
        DEFAULT_LIMITS,
    )

    assert runner.deadline_seconds == DEFAULT_DEADLINE_SECONDS
    assert runner.limits["turns"] == DEFAULT_LIMITS["turns"]


def test_default_bedrock_model_reads_full_environment(monkeypatch) -> None:
    from glide.agent.strands_runner import default_bedrock_model

    captured: dict[str, object] = {}

    class FakeModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("glide.agent.strands_runner.ToolFirstBedrockModel", FakeModel)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-id")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4096")

    default_bedrock_model()

    assert captured == {
        "model_id": "model-id",
        "region_name": "eu-west-2",
        "max_tokens": 4096,
    }


def test_usage_dict_returns_empty_when_metrics_unavailable() -> None:
    from types import SimpleNamespace

    from glide.agent.strands_runner import _usage_dict

    assert _usage_dict(SimpleNamespace(metrics=None)) == {}


def test_invoke_maps_unexpected_errors_and_rethrows_agent_errors() -> None:
    from threading import Event

    from glide.agent.strands_runner import (
        AgentDeadlineExceeded,
        AgentInvocationError,
    )

    def raising(error):
        def invoke(prompt, *, limits=None, cancel_signal=None):
            raise error

        return invoke

    runner = StrandsAgentRunner(agent_factory=lambda *args: None)

    with pytest.raises(AgentInvocationError, match="agent invocation failed"):
        runner._invoke(raising(ValueError("boom")), "prompt", Event())

    sentinel = AgentDeadlineExceeded("deadline")
    with pytest.raises(AgentDeadlineExceeded) as caught:
        runner._invoke(raising(sentinel), "prompt", Event())
    assert caught.value is sentinel


def test_invoke_logs_limit_stop_reason_and_returns_result(caplog) -> None:
    from threading import Event

    runner = StrandsAgentRunner(agent_factory=lambda *args: None)

    def invoker(prompt, *, limits=None, cancel_signal=None):
        return InvocationResult(stop_reason="limit_turns")

    result = runner._invoke(invoker, "prompt", Event())

    assert result.stop_reason == "limit_turns"
    assert "budget cap reached" in caplog.text


def test_check_deadline_raises_once_deadline_passed() -> None:
    import time

    runner = StrandsAgentRunner(agent_factory=lambda *args: None, deadline_seconds=1)

    with pytest.raises(AgentDeadlineExceeded):
        runner._check_deadline(time.monotonic() - 2)


def test_live_model_forces_tool_choice_on_every_turn(monkeypatch) -> None:
    """Regression: text-only turns burned the live loop's turn budget."""

    from glide.agent.strands_runner import ToolFirstBedrockModel
    from strands.models.bedrock import BedrockModel

    captured: dict[str, object] = {}

    def fake_stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        captured.update(kwargs)

        async def empty():
            return
            yield  # pragma: no cover - makes this an async generator

        return empty()

    monkeypatch.setattr(BedrockModel, "stream", fake_stream)
    model = ToolFirstBedrockModel(model_id="model-id", region_name="eu-west-1")

    model.stream(messages=[], tool_specs=None, system_prompt="prompt")
    assert captured["tool_choice"] == {"any": {}}

    model.stream(
        messages=[],
        tool_specs=None,
        system_prompt="prompt",
        tool_choice={"tool": {"name": "read_schedule"}},
    )
    assert captured["tool_choice"] == {"tool": {"name": "read_schedule"}}
