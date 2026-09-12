from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TravelMode(StrEnum):
    DRIVING = "driving"


class EventStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Transparency(StrEnum):
    OPAQUE = "opaque"
    TRANSPARENT = "transparent"


class Attendance(StrEnum):
    ACCEPTED = "accepted"
    TENTATIVE = "tentative"
    DECLINED = "declined"
    NONE = "none"


class EventKind(StrEnum):
    PHYSICAL = "physical"
    VIRTUAL = "virtual"
    UNKNOWN = "unknown"


class StoragePolicyStatus(StrEnum):
    EPHEMERAL = "ephemeral"
    STORAGE_ALLOWED = "storage_allowed"


class TimingConstraint(StrEnum):
    ARRIVE_BY = "arrive_by"
    DEPART_AT = "depart_at"


class PlanAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    REMOVE = "remove"
    NOOP = "noop"
    DECISION = "decision"
    SKIP = "skip"


class DecisionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    STALE = "stale"


class RunStatus(StrEnum):
    QUEUED = "queued"
    READING = "reading"
    PLANNING = "planning"
    APPLYING = "applying"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    PAUSED = "paused"


class MutationOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    REMOVE = "remove"
    NOOP = "noop"


class MutationOutcome(StrEnum):
    APPLIED = "applied"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class PlaceRef(ContractModel):
    id: str
    provider_id: str | None = None
    label: str
    longitude: Annotated[float | None, Field(ge=-180, le=180)] = None
    latitude: Annotated[float | None, Field(ge=-90, le=90)] = None
    provenance: str
    confirmed: bool
    storage_policy_status: StoragePolicyStatus


class UserSettings(ContractModel):
    user_id: str
    time_zone: str
    source_calendar_id: str
    # Managed travel events are written to the user's primary calendar. The
    # existing field is retained for stored-record/API compatibility and must
    # converge to ``primary``; an old separate-calendar id is preserved only as
    # migration metadata and is never deleted automatically.
    glide_calendar_id: str = "primary"
    legacy_glide_calendar_id: str | None = None
    location_overrides: dict[str, PlaceRef] = Field(default_factory=dict)
    start_place: PlaceRef | None = None
    earliest_departure: time | None = None
    mode: TravelMode = TravelMode.DRIVING
    padding_minutes: Annotated[int, Field(ge=0, le=60)] = 10
    # Decision notifications are opt-in contact details. ``notify_on_decisions``
    # lets a user keep the address on file while pausing the "needs your
    # decision" message, and it defaults on so a connected Google account can
    # be notified at the address it signed in with.
    notification_email: str | None = None
    notify_on_decisions: bool = True
    enabled: bool = False
    revision: Annotated[int, Field(ge=1)] = 1


class CalendarEvent(ContractModel):
    provider_event_id: str
    occurrence_id: str
    calendar_id: str
    etag: str
    start: datetime
    end: datetime
    original_time_zone: str
    title: str
    location: str | None = None
    place_id: str | None = None
    status: EventStatus = EventStatus.CONFIRMED
    transparency: Transparency = Transparency.OPAQUE
    attendance: Attendance = Attendance.ACCEPTED
    kind: EventKind
    all_day: bool = False

    @model_validator(mode="after")
    def validate_interval(self) -> CalendarEvent:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("calendar event times must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("calendar event end must be after start")
        return self


class RouteEstimate(ContractModel):
    id: str
    origin_place_id: str
    destination_place_id: str
    mode: TravelMode
    timing_constraint: TimingConstraint
    constraint_time: datetime
    duration_seconds: Annotated[int, Field(gt=0)]
    provider: str
    observed_at: datetime
    quality: str = "fixture"
    available: bool = True


class JourneyPlan(ContractModel):
    journey_key: str
    origin_occurrence_id: str
    destination_occurrence_id: str
    source_calendar_id: str
    source_etags: dict[str, str]
    route_estimate_id: str
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    padding_minutes: Annotated[int, Field(ge=0)]
    action: PlanAction
    reason_code: str
    calculated_facts: dict[str, int | str | bool] = Field(default_factory=dict)


class ManagedBlock(ContractModel):
    journey_key: str
    user_id: str = ""
    origin_occurrence_id: str = ""
    destination_occurrence_id: str = ""
    provider_event_id: str
    start: datetime
    end: datetime
    last_applied_hash: str
    etag: str
    source_revision: str
    policy_revision: int
    padding_minutes: Annotated[int, Field(ge=0)] = 0
    manual_override: bool = False
    skipped: bool = False


class Decision(ContractModel):
    id: str
    user_id: str
    occurrence_id: str
    journey_key: str
    source_revision: str
    reason: str
    calculated_facts: dict[str, int | str | bool]
    allowed_actions: tuple[str, ...]
    status: DecisionStatus = DecisionStatus.OPEN
    version: Annotated[int, Field(ge=1)] = 1
    resolution: str | None = None
    # An optional note the user typed with their answer. It is context for the
    # audit trail, never an instruction the planner reads.
    resolution_note: str | None = None
    # Set once the "needs your decision" notification has been handed to a
    # provider. It is the dedupe mark: a decision is only ever announced once,
    # no matter how many scheduled polls re-observe it.
    notified_at: datetime | None = None


class Run(ContractModel):
    id: str
    user_id: str
    trigger: str
    status: RunStatus
    lease_revision: Annotated[int, Field(ge=1)]
    source_fingerprint: str
    started_at: datetime
    ended_at: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    safe_failure_code: str | None = None


class MutationReceipt(ContractModel):
    id: str
    run_id: str
    journey_key: str
    operation: MutationOperation
    provider_event_id: str | None
    before_hash: str | None
    after_hash: str | None
    outcome: MutationOutcome
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkflowResult(ContractModel):
    run: Run
    plans: tuple[JourneyPlan, ...]
    decisions: tuple[Decision, ...]
    travel_blocks: tuple[ManagedBlock, ...]
    receipts: tuple[MutationReceipt, ...]
    source_events: tuple[CalendarEvent, ...]


class SampleSnapshot(ContractModel):
    """Durable snapshot of one synthetic tenant's mutable state."""

    user_id: str
    session_id: str
    day: date
    source_events: tuple[CalendarEvent, ...]
    skipped_journeys: tuple[str, ...]
    generation: int
    forced_journeys: tuple[str, ...] = ()
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=24)
    )


JsonObject = dict[str, Any]
IsoDate = Literal["YYYY-MM-DD"]
