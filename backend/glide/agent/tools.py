"""Typed tool contracts shared by the model loop and deterministic code.

The plan requires the model to propose evidence-backed actions while
deterministic code owns arithmetic, permissions, conflict checks, and writes.
These schemas are deliberately strict: unknown references are rejected, and no
tool exposes a generic write primitive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadScheduleInput(ToolContract):
    run_id: str
    window_start: datetime
    window_end: datetime


class ScheduleEvent(ToolContract):
    occurrence_id: str
    start: datetime
    end: datetime
    title: str
    location: str | None = None
    kind: Literal["physical", "virtual", "unknown"]
    status: str
    transparency: str


class JourneyPair(ToolContract):
    """A consecutive physical journey with server-bound identities and times.

    The journey key and every occurrence/time field are computed by
    deterministic code and supplied to the model; none of them may be
    invented. Location text is the raw calendar value for ``lookup_place``.
    ``origin_place_id``/``destination_place_id`` carry the server's own
    resolved references when they exist, so the model can request a route
    without re-resolving a place the server already knows.
    """

    journey_key: str
    origin_occurrence_id: str
    destination_occurrence_id: str
    origin_location: str | None = None
    destination_location: str | None = None
    origin_place_id: str | None = None
    destination_place_id: str | None = None
    suggested_action: str | None = None
    suggested_reason: str | None = None
    origin_available: datetime
    destination_start: datetime
    destination_arrival_target: datetime


class ReadScheduleOutput(ToolContract):
    events: list[ScheduleEvent]
    busy_intervals: list[tuple[datetime, datetime]]
    journey_pairs: list[JourneyPair] = Field(default_factory=list)
    scope_exceeded: bool = False


class LookupPlaceInput(ToolContract):
    query: str = Field(min_length=1, max_length=500)
    region: str | None = Field(default=None, max_length=100)


class PlaceCandidate(ToolContract):
    id: str
    provider_id: str | None = None
    label: str
    longitude: float | None = None
    latitude: float | None = None
    provenance: str
    confirmed: bool = False


class LookupPlaceOutput(ToolContract):
    candidates: list[PlaceCandidate] = Field(default_factory=list, max_length=3)
    confirmed_alias: PlaceCandidate | None = None
    reason: str | None = None


class EstimateJourneyInput(ToolContract):
    origin_place_id: str
    destination_place_id: str
    mode: Literal["driving"]
    timing: Literal["depart_at", "arrive_by"]
    timing_time: datetime


class EstimateJourneyOutput(ToolContract):
    estimate_id: str
    duration_seconds: int = Field(ge=0)
    provider: str
    observed_at: datetime | None = None
    available: bool = True
    reason: str | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> EstimateJourneyOutput:
        if self.available and (self.duration_seconds <= 0 or self.observed_at is None):
            raise ValueError("available estimates require a positive duration and observation time")
        return self


class EvaluateCandidateInput(ToolContract):
    origin_available: datetime
    destination_start: datetime
    estimate_id: str
    padding_minutes: int = Field(ge=0, le=60)


class EvaluateCandidateOutput(ToolContract):
    # Echoed back so a proposal can copy the exact reference it evaluated; the
    # prompt asks for that copy, and without the id here the deployed model
    # submitted proposals with a missing or invented route_estimate_id.
    estimate_id: str
    feasible: bool
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    wait_seconds: int = 0
    available_seconds: int = 0
    required_seconds: int = 0
    shortfall_seconds: int = 0
    reason_code: str


class RequestDecisionInput(ToolContract):
    journey_key: str
    occurrence_id: str
    reason_code: str
    facts: dict[str, int | str | bool]
    allowed_actions: list[str]


class RequestDecisionOutput(ToolContract):
    decision_id: str
    status: Literal["open"]


class PlannedJourney(ToolContract):
    """One journey in a model proposal.

    Only ``create``, ``remove``, and ``decision`` are accepted from the model.
    ``update``/``noop``/``skip`` are executor outcomes: advertising them in the
    schema let a model submit a proposal the host could only reject.
    """

    journey_key: str
    origin_occurrence_id: str
    destination_occurrence_id: str
    action: Literal["create", "remove", "decision"]
    reason_code: str
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    route_estimate_id: str | None = None
    evidence: dict[str, int | str | bool] = Field(default_factory=dict)


class ProposePlanInput(ToolContract):
    run_id: str
    journeys: list[PlannedJourney]
    summary: str = Field(max_length=1000)


class ProposePlanOutput(ToolContract):
    accepted: bool
    journeys: list[PlannedJourney]
    reason: str | None = None


def tool_input_schema(model: type[ToolContract]) -> dict[str, object]:
    """Return the Strands ``inputSchema`` envelope for one typed contract."""

    return {"json": model.model_json_schema()}
