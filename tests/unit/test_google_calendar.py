from __future__ import annotations

from datetime import UTC, datetime

from glide.adapters.google_calendar import (
    GoogleCalendarAdapter,
    map_google_event,
)
from glide.domain.models import ManagedBlock


def test_map_google_event_handles_recurrence_and_status() -> None:
    event = {
        "id": "instance-id",
        "recurringEventId": "series-id",
        "originalStartTime": {"dateTime": "2026-09-09T09:00:00+01:00"},
        "start": {"dateTime": "2026-09-09T09:00:00+01:00", "timeZone": "Europe/London"},
        "end": {"dateTime": "2026-09-09T10:00:00+01:00", "timeZone": "Europe/London"},
        "summary": "Client visit",
        "location": "Northside Community Centre",
        "status": "cancelled",
        "transparency": "transparent",
        "attendees": [{"self": True, "responseStatus": "declined"}],
    }

    mapped = map_google_event(event, "primary")

    assert mapped.occurrence_id.startswith("series-id:")
    assert mapped.status.value == "cancelled"
    assert mapped.transparency.value == "transparent"
    assert mapped.attendance.value == "declined"
    assert mapped.kind.value == "physical"
    assert mapped.original_time_zone == "Europe/London"


def test_map_google_event_marks_all_day_events() -> None:
    event = {
        "id": "all-day-1",
        "start": {"date": "2026-09-09", "timeZone": "Europe/London"},
        "end": {"date": "2026-09-10", "timeZone": "Europe/London"},
        "summary": "Conference",
        "location": "Conference Centre",
    }

    mapped = map_google_event(event, "primary")

    assert mapped.all_day is True
    assert mapped.kind.value == "physical"


def test_moved_recurring_instance_keeps_a_distinct_occurrence_id() -> None:
    base = {
        "id": "instance-id",
        "recurringEventId": "series-id",
        "start": {"dateTime": "2026-09-09T09:00:00+01:00"},
        "end": {"dateTime": "2026-09-09T10:00:00+01:00"},
        "summary": "Stand-up",
        "location": "Office",
    }
    original = map_google_event(
        {**base, "originalStartTime": {"dateTime": "2026-09-09T09:00:00+01:00"}},
        "primary",
    )
    moved = map_google_event(
        {**base, "originalStartTime": {"dateTime": "2026-09-10T11:00:00+01:00"}},
        "primary",
    )

    assert original.occurrence_id == "series-id:2026-09-09T09:00:00+01:00"
    assert moved.occurrence_id == "series-id:2026-09-10T11:00:00+01:00"
    assert original.occurrence_id != moved.occurrence_id


def test_event_without_location_or_conference_is_unknown() -> None:
    mapped = map_google_event(
        {
            "id": "bare",
            "start": {"dateTime": "2026-09-09T09:00:00Z"},
            "end": {"dateTime": "2026-09-09T09:30:00Z"},
            "summary": "Quiet time",
        },
        "primary",
    )

    assert mapped.kind.value == "unknown"
    assert mapped.location is None


def test_list_events_paginates_through_list_next() -> None:
    pages = [
        {
            "items": [
                {
                    "id": "one",
                    "start": {"dateTime": "2026-09-09T09:00:00Z"},
                    "end": {"dateTime": "2026-09-09T09:30:00Z"},
                    "summary": "First",
                }
            ]
        },
        {
            "items": [
                {
                    "id": "two",
                    "start": {"dateTime": "2026-09-09T10:00:00Z"},
                    "end": {"dateTime": "2026-09-09T10:30:00Z"},
                    "summary": "Second",
                }
            ]
        },
    ]

    class Request:
        def __init__(self, page) -> None:
            self.page = page

        def execute(self):
            return self.page

    class Events:
        def list(self, **kwargs):
            return Request(pages[0])

        def list_next(self, request, page):
            index = pages.index(page)
            return Request(pages[index + 1]) if index + 1 < len(pages) else None

    class Service:
        def events(self):
            return Events()

    adapter = GoogleCalendarAdapter.__new__(GoogleCalendarAdapter)
    adapter._service = Service()

    events = adapter.list_events(
        calendar_id="primary",
        window_start=datetime(2026, 9, 9, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
    )

    assert [event.occurrence_id for event in events] == ["one", "two"]


def test_adapter_reuses_travel_calendar_and_uses_conditional_etag() -> None:
    captured: dict[str, object] = {}

    class UpdateRequest:
        def execute(self, headers=None):
            captured["update_headers"] = headers
            return {"etag": "new-etag"}

    class DeleteRequest:
        def execute(self, headers=None):
            captured["delete_headers"] = headers
            return None

    class Events:
        def update(self, **kwargs):
            captured["update_kwargs"] = kwargs
            return UpdateRequest()

        def delete(self, **kwargs):
            captured["delete_kwargs"] = kwargs
            return DeleteRequest()

    class CalendarList:
        def list(self, **kwargs):
            class Request:
                def execute(self):
                    return {"items": [{"id": "travel-id", "summary": "Glide Travel"}]}

            return Request()

    class Service:
        def events(self):
            return Events()

        def calendarList(self):
            return CalendarList()

    adapter = GoogleCalendarAdapter.__new__(GoogleCalendarAdapter)
    adapter._service = Service()
    block = ManagedBlock(
        journey_key="key",
        user_id="google:subject",
        destination_occurrence_id="occ_b",
        provider_event_id="event-id",
        start=datetime(2026, 9, 9, 9, 25, tzinfo=UTC),
        end=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        last_applied_hash="hash",
        etag="old-etag",
        source_revision="revision",
        policy_revision=1,
    )

    assert adapter.ensure_travel_calendar() == "travel-id"
    adapter.update_block(calendar_id="travel-id", block=block, expected_etag="old-etag")
    adapter.delete_block(calendar_id="travel-id", event_id="event-id", expected_etag="new-etag")

    assert captured["update_headers"] == {"If-Match": "old-etag"}
    assert captured["delete_headers"] == {"If-Match": "new-etag"}


def test_event_body_stores_ownership_and_hash_properties() -> None:
    adapter = GoogleCalendarAdapter.__new__(GoogleCalendarAdapter)
    block = ManagedBlock(
        journey_key="key",
        user_id="google:subject",
        destination_occurrence_id="occ_b",
        provider_event_id="event-id",
        start=datetime(2026, 9, 9, 9, 25, tzinfo=UTC),
        end=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        last_applied_hash="hash",
        etag="etag",
        source_revision="revision",
        policy_revision=3,
        padding_minutes=10,
    )

    private = adapter._event_body(block)["extendedProperties"]["private"]

    assert private["glideJourneyKey"] == "key"
    assert private["glideUser"] == "google:subject"
    assert private["glideDestinationOccurrence"] == "occ_b"
    assert private["glideAppliedHash"] == "hash"
    assert private["glideSourceRevision"] == "revision"
    assert private["glidePolicyRevision"] == "3"
    assert private["glidePadding"] == "10"
    assert private["glideSchemaVersion"] == "1"


def test_list_blocks_reads_only_owned_marked_events() -> None:
    class Events:
        def list(self, **kwargs):
            return self

        def execute(self):
            return {
                "items": [
                    {
                        "id": "travel-1",
                        "start": {"dateTime": "2026-09-09T09:25:00Z"},
                        "end": {"dateTime": "2026-09-09T10:00:00Z"},
                        "etag": "etag-1",
                        "extendedProperties": {
                            "private": {
                                "glideSchemaVersion": "1",
                                "glideJourneyKey": "key-1",
                                "glideUser": "google:subject",
                                "glideDestinationOccurrence": "occ_b",
                                "glideAppliedHash": "hash-1",
                                "glideSourceRevision": "revision",
                                "glidePolicyRevision": "2",
                                "glidePadding": "10",
                            }
                        },
                    },
                    {
                        "id": "unowned",
                        "start": {"dateTime": "2026-09-09T09:00:00Z"},
                        "end": {"dateTime": "2026-09-09T09:30:00Z"},
                    },
                ]
            }

        def list_next(self, request, page):
            return None

    class Service:
        def events(self):
            return Events()

    adapter = GoogleCalendarAdapter.__new__(GoogleCalendarAdapter)
    adapter._service = Service()

    blocks = adapter.list_blocks(
        calendar_id="travel-id",
        window_start=datetime(2026, 9, 9, tzinfo=UTC),
        window_end=datetime(2026, 9, 10, tzinfo=UTC),
    )

    assert len(blocks) == 1
    block = blocks[0]
    assert block.journey_key == "key-1"
    assert block.user_id == "google:subject"
    assert block.destination_occurrence_id == "occ_b"
    assert block.last_applied_hash == "hash-1"
    assert block.source_revision == "revision"
    assert block.policy_revision == 2
    assert block.padding_minutes == 10
