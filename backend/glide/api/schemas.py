"""Wire models for the public API.

These are deliberately thin. The frozen domain contracts in
``glide.domain.models`` remain the source of truth; these DTOs exist only to
make HTTP boundaries explicit and avoid leaking internal state.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    time_zone: str | None = Field(default=None, max_length=64)
    # Decision notifications. The address is delivered to Amazon SES, so it is
    # validated here rather than interpolated into any provider call.
    notification_email: str | None = Field(default=None, max_length=254)
    notify_on_decisions: bool | None = None

    @field_validator("notification_email")
    @classmethod
    def _notification_email_must_look_like_an_address(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        local, separator, domain = candidate.partition("@")
        if (
            not separator
            or not local
            or "." not in domain
            or any(character.isspace() for character in candidate)
            or "," in candidate
        ):
            raise ValueError("notification_email must be a single email address")
        return candidate

    @field_validator("time_zone")
    @classmethod
    def _time_zone_must_be_iana(cls, value: str | None) -> str | None:
        """S16/F16: the zone is interpolated into the agent prompt."""

        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("time_zone must be an IANA time zone name") from exc
        return value


class ResolveDecisionRequest(WireModel):
    action: str
    place: dict[str, object] | None = None


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
