from __future__ import annotations

from datetime import UTC, datetime, timedelta

from glide.jobs.schedule_state import ScheduleState, is_due, next_bucket

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def test_first_check_is_due() -> None:
    assert is_due(None, interval_minutes=15, now=NOW) is True


def test_interval_ceiling_blocks_an_early_check() -> None:
    state = ScheduleState(last_scheduled_at=NOW)

    assert is_due(state, interval_minutes=15, now=NOW + timedelta(minutes=14)) is False
    assert is_due(state, interval_minutes=15, now=NOW + timedelta(minutes=15)) is True


def test_while_you_were_away_count_accumulates_and_resets() -> None:
    first = next_bucket(None, last_viewed_at=None, now=NOW)
    assert first.scheduled_since_view == 1

    second = next_bucket(
        first,
        last_viewed_at=NOW - timedelta(minutes=30),
        now=NOW + timedelta(minutes=15),
    )
    assert second.scheduled_since_view == 2

    # The owner looked at the day after the last check, so the next scheduled
    # check starts a fresh count.
    third = next_bucket(
        second,
        last_viewed_at=second.last_scheduled_at + timedelta(minutes=1),
        now=NOW + timedelta(minutes=30),
    )
    assert third.scheduled_since_view == 1
