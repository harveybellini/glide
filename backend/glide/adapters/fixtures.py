"""Isolated synthetic calendar and routing fixtures for offline development.

The values here are fictional and must never be described as live provider
results. The route durations are fixed so the canonical sample is repeatable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    PlaceRef,
    RouteEstimate,
    StoragePolicyStatus,
    TimingConstraint,
    Transparency,
    TravelMode,
)

LONDON = ZoneInfo("Europe/London")
SAMPLE_TIME_ZONE = "Europe/London"
SOURCE_CALENDAR_ID = "fixture-primary"
TRAVEL_CALENDAR_ID = "fixture-glide-travel"


@dataclass(frozen=True)
class FixturePlace:
    ref: PlaceRef
    local_time: time
    duration_minutes: int


def _place(
    place_id: str,
    label: str,
    longitude: float | None = None,
    latitude: float | None = None,
) -> PlaceRef:
    return PlaceRef(
        id=place_id,
        provider_id=place_id,
        label=label,
        longitude=longitude,
        latitude=latitude,
        provenance="synthetic fixture",
        confirmed=True,
        storage_policy_status=StoragePolicyStatus.EPHEMERAL,
    )


PLACES = {
    "a": _place("place_a", "Northside Community Centre"),
    "b": _place("place_b", "Westfield Surgery"),
    "c": _place("place_c", "Oakfield Primary School"),
}


def local_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LONDON)


class FixtureCalendar:
    """A mutable, in-memory source calendar for the canonical sample day."""

    def __init__(self, day: date) -> None:
        self.day = day
        self._etag_counter = 1
        self._records: dict[str, tuple[CalendarEvent, str]] = {
            event.occurrence_id: (event, event.location or "")
            for event in self._initial_events()
        }

    def _next_etag(self) -> str:
        etag = f"fixture-etag-{self._etag_counter}"
        self._etag_counter += 1
        return etag

    def _initial_events(self) -> list[CalendarEvent]:
        return [
            self._event(
                event_id="evt_a",
                occurrence_id="occ_a",
                start=local_datetime(self.day, 9, 0),
                end=local_datetime(self.day, 10, 0),
                title="Client visit",
                location="Northside Community Centre",
            ),
            self._event(
                event_id="evt_b",
                occurrence_id="occ_b",
                start=local_datetime(self.day, 11, 0),
                end=local_datetime(self.day, 11, 30),
                title="Appointment",
                location="Westfield Surgery",
            ),
            self._event(
                event_id="evt_c",
                occurrence_id="occ_c",
                start=local_datetime(self.day, 12, 0),
                end=local_datetime(self.day, 12, 15),
                title="School pickup",
                location="Oakfield Primary School",
            ),
        ]

    @classmethod
    def restored(cls, day: date, events: list[CalendarEvent]) -> FixtureCalendar:
        """Rebuild a mutable calendar from a persisted event snapshot."""

        calendar = cls.__new__(cls)
        calendar.day = day
        calendar._etag_counter = 1
        calendar._records = {
            event.occurrence_id: (event, event.location or "")
            for event in events
        }
        return calendar

    def _event(
        self,
        *,
        event_id: str,
        occurrence_id: str,
        start: datetime,
        end: datetime,
        title: str,
        location: str,
    ) -> CalendarEvent:
        return CalendarEvent(
            provider_event_id=event_id,
            occurrence_id=occurrence_id,
            calendar_id=SOURCE_CALENDAR_ID,
            etag=self._next_etag(),
            start=start.astimezone(UTC),
            end=end.astimezone(UTC),
            original_time_zone=SAMPLE_TIME_ZONE,
            title=title,
            location=location,
            status=EventStatus.CONFIRMED,
            transparency=Transparency.OPAQUE,
            attendance=Attendance.ACCEPTED,
            kind=EventKind.PHYSICAL,
        )

    def events(self) -> list[CalendarEvent]:
        return sorted((event for event, _ in self._records.values()), key=lambda e: e.start)

    def move(
        self,
        occurrence_id: str,
        start: datetime,
        end: datetime,
        new_location: str | None = None,
    ) -> CalendarEvent:
        existing, location = self._records[occurrence_id]
        updated = existing.model_copy(
            update={
                "start": start.astimezone(UTC),
                "end": end.astimezone(UTC),
                "etag": self._next_etag(),
                "location": new_location if new_location is not None else existing.location,
            }
        )
        self._records[occurrence_id] = (
            updated,
            new_location if new_location is not None else location,
        )
        return updated

    def delete(self, occurrence_id: str) -> None:
        del self._records[occurrence_id]


class FixtureRouter:
    """Deterministic routes between known synthetic places."""

    def __init__(self) -> None:
        self._durations: dict[tuple[str, str], int] = {
            ("place_a", "place_b"): 25 * 60,
            ("place_b", "place_c"): 30 * 60,
            ("place_a", "place_c"): 45 * 60,
        }
        self.calls: list[dict[str, object]] = []

    def estimate(
        self,
        *,
        origin_place_id: str,
        destination_place_id: str,
        mode: TravelMode,
        departure_at: datetime | None = None,
        arrival_by: datetime | None = None,
    ) -> RouteEstimate:
        self.calls.append(
            {
                "origin_place_id": origin_place_id,
                "destination_place_id": destination_place_id,
                "mode": mode,
                "departure_at": departure_at,
                "arrival_by": arrival_by,
            }
        )
        try:
            duration = self._durations[(origin_place_id, destination_place_id)]
        except KeyError as exc:
            raise ValueError(
                f"no fixture route between {origin_place_id} and {destination_place_id}"
            ) from exc

        constraint = (
            TimingConstraint.DEPART_AT
            if departure_at is not None
            else TimingConstraint.ARRIVE_BY
        )
        constraint_time = departure_at or arrival_by or datetime.now(UTC)
        return RouteEstimate(
            id=f"fixture-route-{len(self.calls)}",
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            mode=mode,
            timing_constraint=constraint,
            constraint_time=constraint_time,
            duration_seconds=duration,
            provider="fixture",
            observed_at=datetime.now(UTC),
            quality="fixture",
            available=True,
        )


def place_index(events: list[CalendarEvent]) -> dict[str, PlaceRef]:
    location_to_place = {
        "Northside Community Centre": PLACES["a"],
        "Westfield Surgery": PLACES["b"],
        "Oakfield Primary School": PLACES["c"],
    }
    return {
        event.occurrence_id: location_to_place[event.location or ""]
        for event in events
        if event.location in location_to_place
    }


def canonical_settings(user_id: str = "sample-user") -> dict[str, object]:
    return {
        "user_id": user_id,
        "time_zone": SAMPLE_TIME_ZONE,
        "source_calendar_id": SOURCE_CALENDAR_ID,
        "glide_calendar_id": TRAVEL_CALENDAR_ID,
        "start_place": PLACES["a"],
        "earliest_departure": time(6, 0),
        "mode": TravelMode.DRIVING,
        "padding_minutes": 10,
        "enabled": True,
        "revision": 1,
    }
