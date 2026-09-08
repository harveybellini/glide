"""Google Calendar API V3 adapter.

The adapter reads the primary calendar and writes only to the app-created
``Glide Travel`` calendar. It never expands the OAuth scope at runtime.
"""

from __future__ import annotations

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
        del calendar_id  # caller hints are ignored; reuse is by summary
        page = self._service.calendarList().list(maxResults=250).execute()
        for item in page.get("items", []):
            if item.get("summary") == TRAVEL_CALENDAR_SUMMARY:
                return item["id"]
        created = (
            self._service.calendars()
            .insert(body={"summary": TRAVEL_CALENDAR_SUMMARY, "timeZone": "UTC"})
            .execute()
        )
        return created["id"]

    def _event_body(self, block: ManagedBlock) -> dict[str, Any]:
        return {
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
        created = (
            self._service.events()
            .insert(calendarId=calendar_id, body=self._event_body(block))
            .execute()
        )
        return block.model_copy(
            update={"provider_event_id": created["id"], "etag": created.get("etag", "")}
        )

    def get_block(self, *, calendar_id: str, event_id: str) -> ManagedBlock | None:
        try:
            fetched = self._service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as exc:  # noqa: BLE001 - map provider errors below
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                return None
            raise ProviderUnavailableError(str(exc)) from exc
        if fetched.get("status") == "cancelled":
            return None
        return ManagedBlock(
            journey_key=fetched.get("extendedProperties", {})
            .get("private", {})
            .get("glideJourneyKey", ""),
            user_id=fetched.get("extendedProperties", {})
            .get("private", {})
            .get("glideUser", ""),
            origin_occurrence_id=fetched.get("extendedProperties", {})
            .get("private", {})
            .get("glideOriginOccurrence", ""),
            destination_occurrence_id=fetched.get("extendedProperties", {})
            .get("private", {})
            .get("glideDestinationOccurrence", ""),
            provider_event_id=fetched["id"],
            start=_parse_datetime(fetched["start"].get("dateTime")),
            end=_parse_datetime(fetched["end"].get("dateTime")),
            last_applied_hash="unknown",
            etag=fetched.get("etag", ""),
            source_revision="unknown",
            policy_revision=1,
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
        headers = {"If-Match": expected_etag} if expected_etag else None
        try:
            updated = (
                self._service.events()
                .update(
                    calendarId=calendar_id,
                    eventId=block.provider_event_id,
                    body=self._event_body(block),
                )
                .execute(headers=headers)
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "resp", None) is not None and exc.resp.status == 412:
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
        headers = {"If-Match": expected_etag} if expected_etag else None
        try:
            self._service.events().delete(calendarId=calendar_id, eventId=event_id).execute(
                headers=headers
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                raise CalendarNotFoundError(event_id) from exc
            if getattr(exc, "resp", None) is not None and exc.resp.status == 412:
                raise CalendarConflictError("travel block changed since it was read") from exc
            raise ProviderUnavailableError(str(exc)) from exc
