from __future__ import annotations

from datetime import UTC, datetime

import pytest
from glide.adapters.fixtures import PLACES, FixtureRouter
from glide.agent.runner import DeterministicAgentRunner
from glide.agent.tools import (
    EstimateJourneyInput,
    LookupPlaceInput,
    PlannedJourney,
    ProposePlanInput,
)
from pydantic import ValidationError


def test_tool_schemas_reject_invalid_input() -> None:
    with pytest.raises(ValidationError):
        LookupPlaceInput(query="")
    with pytest.raises(ValidationError):
        EstimateJourneyInput(
            origin_place_id="a",
            destination_place_id="b",
            mode="walking",
            timing="arrive_by",
            timing_time=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        )


def test_proposal_schema_advertises_only_acceptable_actions() -> None:
    """The proposal schema must match what the host can accept.

    Regression: it previously advertised update/noop/skip, which the host
    always rejects, so a model could only fail by following the schema.
    """

    schema = ProposePlanInput.model_json_schema()
    actions = schema["$defs"]["PlannedJourney"]["properties"]["action"]["enum"]

    assert set(actions) == {"create", "remove", "decision"}
    with pytest.raises(ValidationError):
        PlannedJourney(
            journey_key="j",
            origin_occurrence_id="a",
            destination_occurrence_id="b",
            action="noop",
        )


def test_deterministic_runner_builds_plans() -> None:
    from glide.adapters.fixtures import (
        FixtureCalendar,
        canonical_settings,
        place_index,
    )
    from glide.domain.models import UserSettings

    calendar = FixtureCalendar(day=datetime(2026, 9, 9, tzinfo=UTC).date())
    events = calendar.events()
    runner = DeterministicAgentRunner()

    plans = runner.run(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index=place_index(events),
        router=FixtureRouter(),
        now=datetime(2026, 9, 9, 6, 0, tzinfo=UTC),
    )

    assert len(plans) == 2
    assert {plan.reason_code for plan in plans} == {"feasible", "insufficient_time"}
    assert PLACES["a"].label == "Northside Community Centre"
