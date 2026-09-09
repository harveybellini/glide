"""Google Calendar API V3 adapter.

The adapter reads the primary calendar and writes only to the app-created
``Glide Travel`` calendar. It never expands the OAuth scope at runtime.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from google.auth.credentials import Credentials
from googleapiclient.discovery import build

from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    ManagedBlock,
    Transparency,
)

TRAVEL_CALENDAR_SUMMARY = "Glide Travel"
EVENT_SUMMARY = "Travel · Glide"
PRIVATE_PROPERTIES = {
    "glideSchemaVersion": "1",
}
PAGE_SIZE = 250


class CalendarConflictError(RuntimeError):
    pass


class CalendarNotFoundError(RuntimeError):
    pass


class ProviderUnavailableError(RuntimeError):
    pass


def deterministic_event_id(journey_key: str, source_revision: str = "") -> str:
    """Return a stable Google-compatible base32hex-subset event id.

    The id is stable within one journey/source-revision pair so an
    uncertain-success retry cannot create a duplicate, while a source edit
    yields a new id and lets a manually deleted journey be reconsidered.
    """

    payload = f"{journey_key}|{source_revision}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "resp", None)
    return getattr(response, "status", None)


def _execute_with_etag(request: Any, expected_etag: str | None) -> Any:
    if expected_etag:
        request.headers["If-Match"] = expected_etag
    return request.execute()


def _parse_datetime(value: str | None, all_day: bool = False) -> datetime:
    if not value:
        raise ValueError("Google event is missing a start or end time")
    if all_day:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _occurrence_id(event: dict[str, Any]) -> str:
    event_id = event.get("id", "unknown")
    recurring = event.get("recurringEventId")
    if not recurring:
        return event_id
    original = event.get("originalStartTime", {})
    original_value = original.get("dateTime") or original.get("date") or event_id
    return f"{recurring}:{original_value}"


def _attendance(event: dict[str, Any]) -> Attendance:
    response = event.get("attendees", [])
    for attendee in response:
        if attendee.get("self") is True:
            status = attendee.get("responseStatus", "none")
            if status in {"accepted", "tentative", "declined"}:
                return Attendance(status)
            return Attendance.NONE
    return Attendance.NONE


def _kind(event: dict[str, Any]) -> EventKind:
    location = bool((event.get("location") or "").strip())
    virtual = bool(event.get("conferenceData") or event.get("hangoutLink"))
    if location and not virtual:
        return EventKind.PHYSICAL
    if virtual and not location:
        return EventKind.VIRTUAL
    return EventKind.UNKNOWN


def map_google_event(event: dict[str, Any], calendar_id: str) -> CalendarEvent:
    start_value = event.get("start", {})
    end_value = event.get("end", {})
    all_day = "date" in start_value or "date" in end_value
    start = _parse_datetime(start_value.get("dateTime") or start_value.get("date"), all_day)
    end = _parse_datetime(end_value.get("dateTime") or end_value.get("date"), all_day)
    if all_day and end <= start:
        # Google all-day end dates are exclusive; normalize an empty day to a
        # zero-length opaque interval rather than inventing a destination.
        end = start + timedelta(hours=24)

    status_value = event.get("status", "confirmed")
    status = (
        EventStatus.CANCELLED
        if status_value == "cancelled"
        else EventStatus.CONFIRMED
    )
    transparency = (
        Transparency.TRANSPARENT
        if event.get("transparency") == "transparent"
        else Transparency.OPAQUE
    )
    return CalendarEvent(
        provider_event_id=event.get("id", "unknown"),
        occurrence_id=_occurrence_id(event),
        calendar_id=calendar_id,
        etag=event.get("etag", ""),
        start=start,
        end=end,
        original_time_zone=start_value.get("timeZone") or "UTC",
        title=event.get("summary") or "(no title)",
        location=event.get("location"),
        place_id=event.get("extendedProperties", {})
        .get("private", {})
        .get("glidePlaceId"),
        status=status,
        transparency=transparency,
        attendance=_attendance(event),
        kind=_kind(event),
        all_day=all_day,
    )


class GoogleCalendarAdapter:
    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials
        self._service = build("calendar", "v3", credentials=credentials)

    def list_events(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarEvent]:
        request = self._service.events().list(
            calendarId=calendar_id,
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=PAGE_SIZE,
        )
        events: list[CalendarEvent] = []
        while request is not None:
            page = request.execute()
            events.extend(
                map_google_event(item, calendar_id)
                for item in page.get("items", [])
            )
            request = self._service.events().list_next(request, page)
        return events

    def ensure_travel_calendar(self, calendar_id: str | None = None) -> str:
        if calendar_id:
            return calendar_id
        created = (
            self._service.calendars()
            .insert(body={"summary": TRAVEL_CALENDAR_SUMMARY, "timeZone": "UTC"})
            .execute()
        )
        return created["id"]

    def _event_body(
        self,
        block: ManagedBlock,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": (
                event_id
                or deterministic_event_id(block.journey_key, block.source_revision)
            ),
            "summary": EVENT_SUMMARY,
            "start": {"dateTime": block.start.isoformat()},
            "end": {"dateTime": block.end.isoformat()},
            "visibility": "private",
            "transparency": "opaque",
            "reminders": {"useDefault": False},
            "extendedProperties": {
                "private": {
                    **PRIVATE_PROPERTIES,
                    "glideJourneyKey": block.journey_key,
                    "glideUser": block.user_id,
                    "glideOriginOccurrence": block.origin_occurrence_id,
                    "glideDestinationOccurrence": block.destination_occurrence_id,
                    "glideAppliedHash": block.last_applied_hash,
                    "glideSourceRevision": block.source_revision,
                    "glidePolicyRevision": str(block.policy_revision),
                    "glidePadding": str(block.padding_minutes),
                }
            },
        }

    def create_block(self, *, calendar_id: str, block: ManagedBlock) -> ManagedBlock:
        event_id = deterministic_event_id(block.journey_key, block.source_revision)
        try:
            created = (
                self._service.events()
                .insert(calendarId=calendar_id, body=self._event_body(block))
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - map provider errors below
            if _status_code(exc) != 409:
                raise ProviderUnavailableError(str(exc)) from exc
            existing = self.get_block(calendar_id=calendar_id, event_id=event_id)
            if (
                existing is None
                or existing.journey_key != block.journey_key
                or existing.user_id != block.user_id
                or existing.source_revision != block.source_revision
            ):
                raise CalendarConflictError(
                    "deterministic travel event id is already owned by another event"
                ) from exc
            return existing
        return block.model_copy(
            update={"provider_event_id": created["id"], "etag": created.get("etag", "")}
        )

    def get_block(self, *, calendar_id: str, event_id: str) -> ManagedBlock | None:
        try:
            fetched = self._service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as exc:  # noqa: BLE001 - map provider errors below
            if _status_code(exc) == 404:
                return None
            raise ProviderUnavailableError(str(exc)) from exc
        if fetched.get("status") == "cancelled":
            return None
        private = fetched.get("extendedProperties", {}).get("private", {})
        return ManagedBlock(
            journey_key=private.get("glideJourneyKey", ""),
            user_id=private.get("glideUser", ""),
            origin_occurrence_id=private.get("glideOriginOccurrence", ""),
            destination_occurrence_id=private.get("glideDestinationOccurrence", ""),
            provider_event_id=fetched["id"],
            start=_parse_datetime(fetched["start"].get("dateTime")),
            end=_parse_datetime(fetched["end"].get("dateTime")),
            last_applied_hash=private.get("glideAppliedHash", ""),
            etag=fetched.get("etag", ""),
            source_revision=private.get("glideSourceRevision", "unknown"),
            policy_revision=int(private.get("glidePolicyRevision", "1")),
            padding_minutes=int(private.get("glidePadding", "0")),
        )

    def list_blocks(
        self,
        *,
        calendar_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[ManagedBlock]:
        request = self._service.events().list(
            calendarId=calendar_id,
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=PAGE_SIZE,
        )
        blocks: list[ManagedBlock] = []
        while request is not None:
            page = request.execute()
            for item in page.get("items", []):
                private = item.get("extendedProperties", {}).get("private", {})
                if private.get("glideSchemaVersion") != "1":
                    continue
                journey_key = private.get("glideJourneyKey")
                if not journey_key:
                    continue
                start_value = item.get("start", {})
                end_value = item.get("end", {})
                blocks.append(
                    ManagedBlock(
                        journey_key=journey_key,
                        user_id=private.get("glideUser", ""),
                        origin_occurrence_id=private.get("glideOriginOccurrence", ""),
                        destination_occurrence_id=private.get(
                            "glideDestinationOccurrence", ""
                        ),
                        provider_event_id=item["id"],
                        start=_parse_datetime(start_value.get("dateTime")),
                        end=_parse_datetime(end_value.get("dateTime")),
                        last_applied_hash=private.get("glideAppliedHash", ""),
                        etag=item.get("etag", ""),
                        source_revision=private.get("glideSourceRevision", "unknown"),
                        policy_revision=int(private.get("glidePolicyRevision", "1")),
                        padding_minutes=int(private.get("glidePadding", "0")),
                    )
                )
            request = self._service.events().list_next(request, page)
        return blocks

    def update_block(
        self,
        *,
        calendar_id: str,
        block: ManagedBlock,
        expected_etag: str | None = None,
    ) -> ManagedBlock:
        try:
            request = self._service.events().update(
                calendarId=calendar_id,
                eventId=block.provider_event_id,
                body=self._event_body(block, event_id=block.provider_event_id),
            )
            updated = _execute_with_etag(request, expected_etag)
        except Exception as exc:  # noqa: BLE001
            if _status_code(exc) == 412:
                raise CalendarConflictError("travel block changed since it was read") from exc
            raise ProviderUnavailableError(str(exc)) from exc
        return block.model_copy(
            update={"etag": updated.get("etag", block.etag)}
        )

    def delete_block(
        self,
        *,
        calendar_id: str,
        event_id: str,
        expected_etag: str | None = None,
    ) -> None:
        try:
            request = self._service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
            )
            _execute_with_etag(request, expected_etag)
        except Exception as exc:  # noqa: BLE001
            if _status_code(exc) == 404:
                raise CalendarNotFoundError(event_id) from exc
            if _status_code(exc) == 412:
                raise CalendarConflictError("travel block changed since it was read") from exc
            raise ProviderUnavailableError(str(exc)) from exc
