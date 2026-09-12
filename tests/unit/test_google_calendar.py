from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from glide.adapters.google_calendar import (
    CalendarConflictError,
    CalendarNotFoundError,
    GoogleCalendarAdapter,
    ProviderUnavailableError,
    deterministic_event_id,
    map_google_event,
)
from glide.domain.models import ManagedBlock


def _provider_error(status: int) -> RuntimeError:
    error = RuntimeError(f"provider returned {status}")
    error.resp = SimpleNamespace(status=status)
    return error


def _block(**overrides) -> ManagedBlock:
    fields = {
        "journey_key": "key",
        "user_id": "google:subject",
        "origin_occurrence_id": "occ_a",
        "destination_occurrence_id": "occ_b",
        "provider_event_id": "event-1",
        "start": datetime(2026, 9, 9, 9, 25, tzinfo=UTC),
        "end": datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        "last_applied_hash": "hash",
        "etag": "etag-1",
        "source_revision": "revision",
        "policy_revision": 1,
        "padding_minutes": 10,
    }
    fields.update(overrides)
    return ManagedBlock(**fields)


def _fetched_block(**private) -> dict:
    defaults = {
        "glideSchemaVersion": "1",
        "glideJourneyKey": "key",
        "glideUser": "google:subject",
        "glideOriginOccurrence": "occ_a",
        "glideDestinationOccurrence": "occ_b",
        "glideAppliedHash": "hash",
        "glideSourceRevision": "revision",
        "glidePolicyRevision": "1",
        "glidePadding": "10",
    }
    defaults.update(private)
    return {
        "id": "event-1",
        "status": "confirmed",
        "etag": "etag-1",
        "start": {"dateTime": "2026-09-09T09:25:00Z"},
        "end": {"dateTime": "2026-09-09T10:00:00Z"},
        "extendedProperties": {"private": defaults},
    }


def _adapter(events, calendars=None):
    adapter = GoogleCalendarAdapter.__new__(GoogleCalendarAdapter)

    class Service:
        def events(self):
            return events

        def calendars(self):
            return calendars

    adapter._service = Service()
    return adapter


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


def test_map_google_event_virtual_only_is_virtual() -> None:
    mapped = map_google_event(
        {
            "id": "call",
            "start": {"dateTime": "2026-09-09T09:00:00Z"},
            "end": {"dateTime": "2026-09-09T09:30:00Z"},
            "conferenceData": {"entryPoints": []},
        },
        "primary",
    )

    assert mapped.kind.value == "virtual"


def test_map_google_event_naive_datetime_becomes_utc() -> None:
    mapped = map_google_event(
        {
            "id": "naive",
            "start": {"dateTime": "2026-09-09T09:00:00"},
            "end": {"dateTime": "2026-09-09T09:30:00"},
        },
        "primary",
    )

    assert mapped.start.tzinfo is UTC
    assert mapped.end.tzinfo is UTC


def test_map_google_event_normalizes_empty_all_day_interval() -> None:
    mapped = map_google_event(
        {
            "id": "all-day-1",
            "start": {"date": "2026-09-09"},
            "end": {"date": "2026-09-09"},
        },
        "primary",
    )

    assert mapped.end == mapped.start + timedelta(hours=24)


def test_map_google_event_rejects_missing_times() -> None:
    with pytest.raises(ValueError, match="missing a start or end time"):
        map_google_event({"id": "broken"}, "primary")


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
                },
                {
                    "id": "glide-owned",
                    "extendedProperties": {
                        "private": {
                            "glideSchemaVersion": "1",
                            "glideJourneyKey": "journey",
                            "glideUser": "google:subject",
                        }
                    },
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


def test_adapter_uses_primary_calendar_and_conditional_etag() -> None:
    captured: dict[str, object] = {}

    class UpdateRequest:
        def __init__(self):
            self.headers = {}

        def execute(self):
            captured["update_headers"] = self.headers
            return {"etag": "new-etag"}

    class DeleteRequest:
        def __init__(self):
            self.headers = {}

        def execute(self):
            captured["delete_headers"] = self.headers
            return None

    class Events:
        def update(self, **kwargs):
            captured["update_kwargs"] = kwargs
            return UpdateRequest()

        def delete(self, **kwargs):
            captured["delete_kwargs"] = kwargs
            return DeleteRequest()

    class Service:
        def events(self):
            return Events()

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

    adapter.update_block(calendar_id="primary", block=block, expected_etag="old-etag")
    adapter.delete_block(calendar_id="primary", event_id="event-id", expected_etag="new-etag")

    assert captured["update_headers"] == {"If-Match": "old-etag"}
    assert captured["delete_headers"] == {"If-Match": "new-etag"}
    assert captured["update_kwargs"]["calendarId"] == "primary"
    assert captured["update_kwargs"]["body"]["colorId"] == "10"
    assert captured["delete_kwargs"]["calendarId"] == "primary"


def test_adapter_has_no_calendar_creation_or_deletion_surface() -> None:
    adapter = _adapter(events=None, calendars=None)

    assert not hasattr(adapter, "ensure_travel_calendar")


def test_create_block_returns_inserted_id_and_etag() -> None:
    class Events:
        def insert(self, **kwargs):
            return SimpleNamespace(
                execute=lambda: {"id": "event-1", "etag": "etag-1"}
            )

    adapter = _adapter(Events())
    created = adapter.create_block(calendar_id="primary", block=_block())

    assert created.provider_event_id == "event-1"
    assert created.etag == "etag-1"


def test_created_blocks_use_google_green_event_colour() -> None:
    captured: dict[str, object] = {}

    class Events:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                execute=lambda: {"id": "event-1", "etag": "etag-1"}
            )

    adapter = _adapter(Events())
    adapter.create_block(calendar_id="primary", block=_block())

    body = captured["body"]
    assert isinstance(body, dict)
    # Google's event colour palette uses string ids; 10 is "Basil" green.
    assert body["colorId"] == "10"


def test_create_block_maps_unexpected_error() -> None:
    class Events:
        def insert(self, **kwargs):
            raise RuntimeError("boom")

    adapter = _adapter(Events())

    with pytest.raises(ProviderUnavailableError):
        adapter.create_block(calendar_id="travel-id", block=_block())


def test_create_block_recovers_an_identical_409() -> None:
    class Events:
        def insert(self, **kwargs):
            raise _provider_error(409)

        def get(self, **kwargs):
            return SimpleNamespace(execute=lambda: _fetched_block())

    adapter = _adapter(Events())
    recovered = adapter.create_block(calendar_id="travel-id", block=_block())

    assert recovered.provider_event_id == "event-1"
    assert recovered.journey_key == "key"


@pytest.mark.parametrize("private", [{}, {"glideJourneyKey": "other"}])
def test_create_block_conflicts_when_409_is_another_event(private) -> None:
    class Events:
        def insert(self, **kwargs):
            raise _provider_error(409)

        def get(self, **kwargs):
            if private == {}:
                raise _provider_error(404)
            return SimpleNamespace(execute=lambda: _fetched_block(**private))

    adapter = _adapter(Events())

    with pytest.raises(CalendarConflictError):
        adapter.create_block(calendar_id="travel-id", block=_block())


def test_get_block_maps_404_and_provider_errors() -> None:
    class Events:
        def __init__(self, error):
            self.error = error

        def get(self, **kwargs):
            raise self.error

    assert _adapter(Events(_provider_error(404))).get_block(
        calendar_id="travel-id", event_id="event-1"
    ) is None
    with pytest.raises(ProviderUnavailableError):
        _adapter(Events(_provider_error(500))).get_block(
            calendar_id="travel-id", event_id="event-1"
        )


def test_get_block_returns_none_for_cancelled_event() -> None:
    class Events:
        def get(self, **kwargs):
            return SimpleNamespace(
                execute=lambda: {**_fetched_block(), "status": "cancelled"}
            )

    adapter = _adapter(Events())

    assert adapter.get_block(calendar_id="travel-id", event_id="event-1") is None


def test_update_block_maps_412_and_provider_errors() -> None:
    class Events:
        def __init__(self, error):
            self.error = error

        def update(self, **kwargs):
            raise self.error

    with pytest.raises(CalendarConflictError):
        _adapter(Events(_provider_error(412))).update_block(
            calendar_id="travel-id", block=_block()
        )
    with pytest.raises(ProviderUnavailableError):
        _adapter(Events(_provider_error(500))).update_block(
            calendar_id="travel-id", block=_block()
        )


def test_delete_block_maps_404_412_and_provider_errors() -> None:
    class Events:
        def __init__(self, error):
            self.error = error

        def delete(self, **kwargs):
            raise self.error

    with pytest.raises(CalendarNotFoundError):
        _adapter(Events(_provider_error(404))).delete_block(
            calendar_id="travel-id", event_id="event-1"
        )
    with pytest.raises(CalendarConflictError):
        _adapter(Events(_provider_error(412))).delete_block(
            calendar_id="travel-id", event_id="event-1"
        )
    with pytest.raises(ProviderUnavailableError):
        _adapter(Events(_provider_error(500))).delete_block(
            calendar_id="travel-id", event_id="event-1"
        )


def test_deterministic_event_id_is_stable_and_google_compatible() -> None:
    first = deterministic_event_id("journey-key")
    assert first == deterministic_event_id("journey-key")
    assert first != deterministic_event_id("another-journey")
    assert first != deterministic_event_id("journey-key", "revision-a")
    assert deterministic_event_id("journey-key", "revision-a") == deterministic_event_id(
        "journey-key", "revision-a"
    )
    assert 5 <= len(first) <= 1024
    assert set(first) <= set("0123456789abcdefghijklmnopqrstuv")


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

    assert adapter._event_body(block)["id"] == deterministic_event_id("key", "revision")
    assert adapter._event_body(block)["colorId"] == "10"
    assert (
        adapter._event_body(block, event_id="event-id")["id"] == "event-id"
    )
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


def test_list_blocks_skips_marked_item_without_journey_key() -> None:
    class Events:
        def list(self, **kwargs):
            return self

        def execute(self):
            return {
                "items": [
                    {
                        "id": "no-journey",
                        "start": {"dateTime": "2026-09-09T09:25:00Z"},
                        "end": {"dateTime": "2026-09-09T10:00:00Z"},
                        "extendedProperties": {
                            "private": {"glideSchemaVersion": "1"}
                        },
                    }
                ]
            }

        def list_next(self, request, page):
            return None

    adapter = _adapter(Events())

    blocks = adapter.list_blocks(
        calendar_id="travel-id",
        window_start=datetime(2026, 9, 9, tzinfo=UTC),
        window_end=datetime(2026, 9, 10, tzinfo=UTC),
    )

    assert blocks == []
