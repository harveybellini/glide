"""Wire models for the public API.

These are deliberately thin. The frozen domain contracts in
``glide.domain.models`` remain the source of truth; these DTOs exist only to
make HTTP boundaries explicit and avoid leaking internal state.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from glide.domain.models import (
    CalendarEvent,
    Decision,
    ManagedBlock,
    MutationReceipt,
    Run,
    UserSettings,
)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(WireModel):
    status: str
    mode: str
    version: str


class CreateDemoSessionRequest(WireModel):
    day: date | None = None


class SessionHandle(WireModel):
    session_id: str


class DemoSessionResponse(WireModel):
    session: SessionHandle
    settings: UserSettings
    source_events: list[CalendarEvent]
    sample_date: date
    label: str


class EventEditRequest(WireModel):
    start: datetime
    end: datetime
    location: str | None = None


class RunRequest(WireModel):
    trigger: str = "sample"


class RunQueuedResponse(WireModel):
    run_id: str
    status: str


class DayResponse(WireModel):
    date: date
    source_events: list[CalendarEvent]
    travel_blocks: list[ManagedBlock]
    decisions: list[Decision]
    last_run: Run | None = None
    label: str


class ActivityResponse(WireModel):
    receipts: list[MutationReceipt]


class SettingsPatch(WireModel):
    padding_minutes: int | None = Field(default=None, ge=0, le=60)
    start_place: dict[str, object] | None = None
    earliest_departure: str | None = None
    enabled: bool | None = None
    time_zone: str | None = None


class ResolveDecisionRequest(WireModel):
    action: str


class ResolveDecisionResponse(WireModel):
    decision: Decision
    run_id: str | None = None


class DecisionListResponse(WireModel):
    decisions: list[Decision]


class RunResultResponse(WireModel):
    run: Run
    plans: list[dict[str, Any]]
    decisions: list[Decision]
    travel_blocks: list[ManagedBlock]
    receipts: list[MutationReceipt]
