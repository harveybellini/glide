"""Deterministic calendar scheduling primitives.

This module owns the arithmetic and free-interval checks described in the plan.
It deliberately contains no provider calls and no persistent state. Providers
and calendars are represented by small protocols so the same code runs against
fixtures now and live adapters later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol

from glide.domain.models import (
    Attendance,
    CalendarEvent,
    EventKind,
    EventStatus,
    JourneyPlan,
    PlaceRef,
    PlanAction,
    RouteEstimate,
    Transparency,
    TravelMode,
    UserSettings,
)


class RouteEstimator(Protocol):
    def estimate(
        self,
        *,
        origin_place_id: str,
        destination_place_id: str,
        mode: TravelMode,
        departure_at: datetime | None = None,
        arrival_by: datetime | None = None,
    ) -> RouteEstimate: ...


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime
    event_id: str

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("busy interval times must be timezone-aware")
        if self.end < self.start:
            raise ValueError("busy interval end must not precede start")


@dataclass(frozen=True)
class FreeGap:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class JourneyInterval:
    start: datetime
    end: datetime
    duration_seconds: int
    wait_seconds: int


@dataclass(frozen=True)
class JourneyOutcome:
    plan: JourneyPlan
    travel_start: datetime | None = None
    travel_end: datetime | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    """Deterministic feasibility result for one timed route estimate."""

    feasible: bool
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    wait_seconds: int = 0
    available_seconds: int = 0
    required_seconds: int = 0
    shortfall_seconds: int = 0
    reason_code: str = "feasible"


@dataclass(frozen=True)
class JourneyPairRef:
    """One consecutive physical journey the planner is expected to cover."""

    journey_key: str
    origin_occurrence_id: str
    destination_occurrence_id: str
    origin_location: str | None
    destination_location: str | None
    origin_available: datetime
    destination_start: datetime
    destination_arrival_target: datetime


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def busy_intervals(events: list[CalendarEvent]) -> list[BusyInterval]:
    """Return intervals that Glide must not schedule inside."""

    intervals: list[BusyInterval] = []
    for event in events:
        if event.status == EventStatus.CANCELLED:
            continue
        if event.transparency == Transparency.TRANSPARENT:
            continue
        if event.attendance == Attendance.DECLINED:
            continue
        intervals.append(
            BusyInterval(
                start=ensure_utc(event.start),
                end=ensure_utc(event.end),
                event_id=event.occurrence_id,
            )
        )
    intervals.sort(key=lambda interval: interval.start)
    return merge_busy_intervals(intervals)


def merge_busy_intervals(intervals: list[BusyInterval]) -> list[BusyInterval]:
    if not intervals:
        return []

    merged: list[BusyInterval] = []
    for interval in intervals:
        if not merged or interval.start > merged[-1].end:
            merged.append(interval)
            continue
        if interval.end > merged[-1].end:
            merged[-1] = BusyInterval(
                start=merged[-1].start,
                end=interval.end,
                event_id=merged[-1].event_id,
            )
    return merged


def free_gaps(
    window_start: datetime,
    window_end: datetime,
    intervals: list[BusyInterval],
) -> list[FreeGap]:
    """Return free, half-open intervals inside ``[window_start, window_end)``."""

    window_start = ensure_utc(window_start)
    window_end = ensure_utc(window_end)
    if window_end <= window_start:
        return []

    gaps: list[FreeGap] = []
    cursor = window_start
    for interval in intervals:
        if interval.end <= window_start:
            continue
        if interval.start >= window_end:
            break
        if interval.start > cursor:
            gaps.append(FreeGap(start=cursor, end=interval.start))
        cursor = max(cursor, interval.end)
    if cursor < window_end:
        gaps.append(FreeGap(start=cursor, end=window_end))
    return gaps


def interval_is_free(
    start: datetime,
    end: datetime,
    intervals: list[BusyInterval],
) -> bool:
    start = ensure_utc(start)
    end = ensure_utc(end)
    for interval in intervals:
        if interval.start < end and start < interval.end:
            return False
    return True


def available_seconds(
    origin_available: datetime,
    arrival_target: datetime,
    intervals: list[BusyInterval],
) -> int:
    gaps = free_gaps(origin_available, arrival_target, intervals)
    return max((int((gap.end - gap.start).total_seconds()) for gap in gaps), default=0)


def travel_block_seconds(
    duration_seconds: int,
    padding_minutes: int,
) -> int:
    return duration_seconds + padding_minutes * 60


def physical_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Return the physical events that can produce a journey, in time order."""

    return [
        event
        for event in sorted(events, key=lambda event: event.start)
        if event.status != EventStatus.CANCELLED
        and event.attendance != Attendance.DECLINED
        and event.kind == EventKind.PHYSICAL
    ]


def hybrid_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Opaque events whose mode is uncertain but that name a location.

    These meetings might be in person or online, so they need a user choice
    instead of being silently treated as either.
    """

    return [
        event
        for event in sorted(events, key=lambda event: event.start)
        if event.status != EventStatus.CANCELLED
        and event.attendance != Attendance.DECLINED
        and event.transparency == Transparency.OPAQUE
        and event.kind == EventKind.UNKNOWN
        and bool((event.location or "").strip())
    ]


def all_day_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Opaque all-day events that need one day-level decision."""

    return [
        event
        for event in sorted(events, key=lambda event: event.start)
        if event.status != EventStatus.CANCELLED
        and event.attendance != Attendance.DECLINED
        and event.transparency == Transparency.OPAQUE
        and event.all_day
    ]


def first_origin_available(
    *,
    first_start: datetime,
    now: datetime,
    earliest_departure: time | None,
) -> datetime:
    """Earliest instant a start-address journey can depart."""

    first_start = ensure_utc(first_start)
    now = ensure_utc(now)
    available = now
    if earliest_departure is not None:
        available = max(
            available,
            datetime.combine(
                first_start.astimezone(now.tzinfo).date(),
                earliest_departure,
                tzinfo=now.tzinfo,
            ),
        )
    return available


def _destination_reference(event: CalendarEvent) -> str:
    return event.occurrence_id


def places_match(origin: PlaceRef, destination: PlaceRef) -> bool:
    """Return True when two place references resolve to the same venue."""

    if origin.id == destination.id:
        return True
    if origin.provider_id and destination.provider_id:
        return origin.provider_id == destination.provider_id
    return False


def _event_place(event: CalendarEvent, place_index: dict[str, PlaceRef]) -> PlaceRef:
    try:
        return place_index[event.occurrence_id]
    except KeyError as exc:
        raise ValueError(f"no place mapping for occurrence {event.occurrence_id}") from exc


def _decision_plan(
    *,
    user_id: str,
    origin_occurrence_id: str,
    destination_occurrence_id: str,
    source_calendar_id: str,
    source_etags: dict[str, str],
    padding_minutes: int,
    mode: TravelMode,
    reason_code: str,
    calculated_facts: dict[str, int | str | bool],
) -> JourneyPlan:
    return JourneyPlan(
        journey_key=journey_key(
            user_id=user_id,
            source_calendar_id=source_calendar_id,
            destination_occurrence_id=destination_occurrence_id,
            mode=mode,
        ),
        origin_occurrence_id=origin_occurrence_id,
        destination_occurrence_id=destination_occurrence_id,
        source_calendar_id=source_calendar_id,
        source_etags=source_etags,
        route_estimate_id="unavailable",
        proposed_start=None,
        proposed_end=None,
        padding_minutes=padding_minutes,
        action=PlanAction.DECISION,
        reason_code=reason_code,
        calculated_facts=calculated_facts,
    )


def plan_journey(
    *,
    user_id: str,
    origin: PlaceRef,
    destination: PlaceRef,
    origin_available: datetime,
    destination_start: datetime,
    destination_occurrence_id: str,
    origin_occurrence_id: str,
    source_calendar_id: str,
    source_etags: dict[str, str],
    padding_minutes: int,
    mode: TravelMode,
    estimator: RouteEstimator,
    busy: list[BusyInterval],
) -> JourneyOutcome:
    """Plan one physical journey and return a create/decision/noop proposal."""

    if places_match(origin, destination):
        return JourneyOutcome(
            plan=JourneyPlan(
                journey_key=journey_key(
                    user_id=user_id,
                    source_calendar_id=source_calendar_id,
                    destination_occurrence_id=destination_occurrence_id,
                    mode=mode,
                ),
                origin_occurrence_id=origin_occurrence_id,
                destination_occurrence_id=destination_occurrence_id,
                source_calendar_id=source_calendar_id,
                source_etags=source_etags,
                route_estimate_id="same-place",
                padding_minutes=padding_minutes,
                action=PlanAction.REMOVE,
                reason_code="same_place",
                calculated_facts={"duration_seconds": 0},
            )
        )

    origin_available = ensure_utc(origin_available)
    destination_start = ensure_utc(destination_start)
    arrival_target = destination_start - timedelta(minutes=padding_minutes)

    arrival_route = estimator.estimate(
        origin_place_id=origin.id,
        destination_place_id=destination.id,
        mode=mode,
        arrival_by=arrival_target,
    )

    def plan_for_interval(interval: JourneyInterval) -> JourneyOutcome:
        facts = {
            "duration_seconds": interval.duration_seconds,
            "padding_minutes": padding_minutes,
            "wait_seconds": interval.wait_seconds,
        }
        return JourneyOutcome(
            plan=JourneyPlan(
                journey_key=journey_key(
                    user_id=user_id,
                    source_calendar_id=source_calendar_id,
                    destination_occurrence_id=destination_occurrence_id,
                    mode=mode,
                ),
                origin_occurrence_id=origin_occurrence_id,
                destination_occurrence_id=destination_occurrence_id,
                source_calendar_id=source_calendar_id,
                source_etags=source_etags,
                route_estimate_id=arrival_route.id,
                proposed_start=interval.start,
                proposed_end=interval.end,
                padding_minutes=padding_minutes,
                action=PlanAction.CREATE,
                reason_code="feasible",
                calculated_facts=facts,
            ),
            travel_start=interval.start,
            travel_end=interval.end,
        )

    latest_departure = arrival_target - timedelta(seconds=arrival_route.duration_seconds)
    if latest_departure >= origin_available and interval_is_free(
        latest_departure,
        destination_start,
        busy,
    ):
        return plan_for_interval(
            JourneyInterval(
                start=latest_departure,
                end=destination_start,
                duration_seconds=arrival_route.duration_seconds,
                wait_seconds=0,
            )
        )

    for gap in reversed(
        free_gaps(origin_available, destination_start, busy)
    ):
        interval = _latest_interval_in_gap(
            gap=gap,
            origin=origin,
            destination=destination,
            mode=mode,
            padding_minutes=padding_minutes,
            estimator=estimator,
            arrival_route=arrival_route,
        )
        if interval is not None:
            return plan_for_interval(interval)

    available = available_seconds(origin_available, destination_start, busy)
    required = travel_block_seconds(arrival_route.duration_seconds, padding_minutes)
    return JourneyOutcome(
        plan=JourneyPlan(
            journey_key=journey_key(
                user_id=user_id,
                source_calendar_id=source_calendar_id,
                destination_occurrence_id=destination_occurrence_id,
                mode=mode,
            ),
            origin_occurrence_id=origin_occurrence_id,
            destination_occurrence_id=destination_occurrence_id,
            source_calendar_id=source_calendar_id,
            source_etags=source_etags,
            route_estimate_id=arrival_route.id,
            proposed_start=None,
            proposed_end=None,
            padding_minutes=padding_minutes,
            action=PlanAction.DECISION,
            reason_code="insufficient_time",
            calculated_facts={
                "available_seconds": available,
                "required_seconds": required,
                "shortfall_seconds": max(required - available, 0),
                "duration_seconds": arrival_route.duration_seconds,
            },
        )
    )


def evaluate_journey_candidate(
    *,
    origin_available: datetime,
    destination_start: datetime,
    duration_seconds: int,
    padding_minutes: int,
    busy: list[BusyInterval],
) -> CandidateEvaluation:
    """Evaluate one timed route estimate without further provider calls.

    The estimate already carries a fixed duration, so placement arithmetic is
    pure: it never re-estimates and therefore cannot surprise the route-call
    budget. It mirrors the ideal-arrival logic of ``plan_journey`` and falls
    back to the latest candidate departure in a free gap when the ideal
    arrival is blocked by an intervening busy interval.
    """

    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")

    origin_available = ensure_utc(origin_available)
    destination_start = ensure_utc(destination_start)
    arrival_target = destination_start - timedelta(minutes=padding_minutes)
    required = travel_block_seconds(duration_seconds, padding_minutes)
    available = available_seconds(origin_available, destination_start, busy)

    latest_departure = arrival_target - timedelta(seconds=duration_seconds)
    if latest_departure >= origin_available and interval_is_free(
        latest_departure,
        destination_start,
        busy,
    ):
        return CandidateEvaluation(
            feasible=True,
            proposed_start=latest_departure,
            proposed_end=destination_start,
            wait_seconds=0,
            available_seconds=available,
            required_seconds=required,
            reason_code="feasible",
        )

    for gap in reversed(free_gaps(origin_available, destination_start, busy)):
        if gap.end - gap.start < timedelta(seconds=required):
            continue
        for departure in _departure_candidates(gap=gap, padding_minutes=padding_minutes):
            block_end = departure + timedelta(seconds=required)
            if block_end <= gap.end and departure >= gap.start:
                return CandidateEvaluation(
                    feasible=True,
                    proposed_start=departure,
                    proposed_end=block_end,
                    wait_seconds=0,
                    available_seconds=available,
                    required_seconds=required,
                    reason_code="feasible",
                )

    shortfall = max(required - available, 0)
    return CandidateEvaluation(
        feasible=False,
        available_seconds=available,
        required_seconds=required,
        shortfall_seconds=shortfall,
        reason_code="insufficient_time",
    )


def _latest_interval_in_gap(
    *,
    gap: FreeGap,
    origin: PlaceRef,
    destination: PlaceRef,
    mode: TravelMode,
    padding_minutes: int,
    estimator: RouteEstimator,
    arrival_route: RouteEstimate,
) -> JourneyInterval | None:
    """Find the latest feasible travel interval in one free gap.

    The ideal plan arrives just before the destination. If a busy interval sits
    between an earlier free gap and the destination, the block covers driving
    plus padding only, and the remaining waiting time is reported separately.
    """

    if gap.end - gap.start < timedelta(seconds=arrival_route.duration_seconds):
        return None

    candidates = _departure_candidates(gap=gap, padding_minutes=padding_minutes)
    for departure in candidates:
        route = estimator.estimate(
            origin_place_id=origin.id,
            destination_place_id=destination.id,
            mode=mode,
            departure_at=departure,
        )
        block_end = departure + timedelta(
            seconds=travel_block_seconds(route.duration_seconds, padding_minutes)
        )
        if block_end <= gap.end and departure >= gap.start:
            return JourneyInterval(
                start=departure,
                end=block_end,
                duration_seconds=route.duration_seconds,
                wait_seconds=0,
            )
    return None


def _departure_candidates(gap: FreeGap, padding_minutes: int) -> list[datetime]:
    """Return a small ordered set of departure times, latest first."""

    latest = gap.end - timedelta(minutes=padding_minutes)
    earliest = gap.start
    candidates: list[datetime] = []
    if latest >= earliest:
        candidates.append(latest)
    middle = earliest + (gap.end - earliest) / 2
    if middle < latest and middle > earliest:
        candidates.append(middle)
    if earliest not in candidates:
        candidates.append(earliest)
    # Keep unique instants while preserving descending order.
    unique: list[datetime] = []
    seen: set[datetime] = set()
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return sorted(unique, reverse=True)


def build_journey_plans(
    *,
    settings: UserSettings,
    events: list[CalendarEvent],
    place_index: dict[str, PlaceRef],
    estimator: RouteEstimator,
    now: datetime,
) -> list[JourneyPlan]:
    """Build plans for all consecutive physical journeys in one run."""

    events = sorted(events, key=lambda event: event.start)
    busy = busy_intervals(events)
    plans: list[JourneyPlan] = []
    source_etags = {event.occurrence_id: event.etag for event in events}

    physical = physical_events(events)
    chain_blocked = False

    if physical:
        first = physical[0]
        first_place = place_index.get(first.occurrence_id)
        first_available = first_origin_available(
            first_start=first.start,
            now=now,
            earliest_departure=settings.earliest_departure,
        )

        # The first journey is home -> first physical event only when the
        # starting address differs from that event's location. This keeps the
        # canonical fixture free of an artificial home block when the sample
        # starts at A.
        if first_place is None:
            plans.append(
                _decision_plan(
                    user_id=settings.user_id,
                    origin_occurrence_id="start_place",
                    destination_occurrence_id=first.occurrence_id,
                    source_calendar_id=settings.source_calendar_id,
                    source_etags=source_etags,
                    padding_minutes=settings.padding_minutes,
                    mode=settings.mode,
                    reason_code="unknown_location",
                    calculated_facts={"occurrence_id": first.occurrence_id},
                )
            )
            chain_blocked = True
        elif settings.start_place is None:
            plans.append(
                _decision_plan(
                    user_id=settings.user_id,
                    origin_occurrence_id="start_place",
                    destination_occurrence_id=first.occurrence_id,
                    source_calendar_id=settings.source_calendar_id,
                    source_etags=source_etags,
                    padding_minutes=settings.padding_minutes,
                    mode=settings.mode,
                    reason_code="unknown_start",
                    calculated_facts={},
                )
            )
        elif not places_match(settings.start_place, first_place):
            outcome = plan_journey(
                user_id=settings.user_id,
                origin=settings.start_place,
                destination=first_place,
                origin_available=first_available,
                destination_start=first.start,
                destination_occurrence_id=first.occurrence_id,
                origin_occurrence_id="start_place",
                source_calendar_id=settings.source_calendar_id,
                source_etags=source_etags,
                padding_minutes=settings.padding_minutes,
                mode=settings.mode,
                estimator=estimator,
                busy=busy,
            )
            plans.append(outcome.plan)
            chain_blocked = chain_blocked or outcome.plan.action == PlanAction.DECISION

        for previous, current in zip(physical, physical[1:], strict=False):
            if chain_blocked:
                plans.append(
                    _decision_plan(
                        user_id=settings.user_id,
                        origin_occurrence_id=previous.occurrence_id,
                        destination_occurrence_id=current.occurrence_id,
                        source_calendar_id=settings.source_calendar_id,
                        source_etags=source_etags,
                        padding_minutes=settings.padding_minutes,
                        mode=settings.mode,
                        reason_code="downstream_uncertain",
                        calculated_facts={
                            "origin_occurrence_id": previous.occurrence_id,
                            "destination_occurrence_id": current.occurrence_id,
                        },
                    )
                )
                continue
            origin_place = place_index.get(previous.occurrence_id)
            destination_place = place_index.get(current.occurrence_id)
            if origin_place is None or destination_place is None:
                plans.append(
                    _decision_plan(
                        user_id=settings.user_id,
                        origin_occurrence_id=previous.occurrence_id,
                        destination_occurrence_id=current.occurrence_id,
                        source_calendar_id=settings.source_calendar_id,
                        source_etags=source_etags,
                        padding_minutes=settings.padding_minutes,
                        mode=settings.mode,
                        reason_code="unknown_location",
                        calculated_facts={
                            "origin_occurrence_id": previous.occurrence_id,
                            "destination_occurrence_id": current.occurrence_id,
                        },
                    )
                )
                chain_blocked = True
                continue
            outcome = plan_journey(
                user_id=settings.user_id,
                origin=origin_place,
                destination=destination_place,
                origin_available=previous.end,
                destination_start=current.start,
                destination_occurrence_id=current.occurrence_id,
                origin_occurrence_id=previous.occurrence_id,
                source_calendar_id=settings.source_calendar_id,
                source_etags=source_etags,
                padding_minutes=settings.padding_minutes,
                mode=settings.mode,
                estimator=estimator,
                busy=busy,
            )
            plans.append(outcome.plan)
            chain_blocked = chain_blocked or outcome.plan.action == PlanAction.DECISION

    for event in hybrid_events(events):
        plans.append(
            _decision_plan(
                user_id=settings.user_id,
                origin_occurrence_id=event.occurrence_id,
                destination_occurrence_id=event.occurrence_id,
                source_calendar_id=settings.source_calendar_id,
                source_etags=source_etags,
                padding_minutes=settings.padding_minutes,
                mode=settings.mode,
                reason_code="hybrid_meeting",
                calculated_facts={
                    "occurrence_id": event.occurrence_id,
                    "location": event.location or "",
                },
            )
        )

    for event in all_day_events(events):
        plans.append(
            _decision_plan(
                user_id=settings.user_id,
                origin_occurrence_id=event.occurrence_id,
                destination_occurrence_id=event.occurrence_id,
                source_calendar_id=settings.source_calendar_id,
                source_etags=source_etags,
                padding_minutes=settings.padding_minutes,
                mode=settings.mode,
                reason_code="all_day",
                calculated_facts={"occurrence_id": event.occurrence_id},
            )
        )

    return plans


def journey_pairs(
    *,
    settings: UserSettings,
    events: list[CalendarEvent],
    place_index: dict[str, PlaceRef],
    now: datetime,
) -> list[JourneyPairRef]:
    """Return the consecutive physical journeys one run must cover.

    This mirrors ``build_journey_plans`` selection: a start-address pair is
    included only when the starting address differs from the first physical
    event, and every consecutive physical event pair is present even when a
    place cannot yet be resolved (those become explicit decisions).
    """

    pairs: list[JourneyPairRef] = []

    def add_pair(
        origin_occurrence_id: str,
        destination: CalendarEvent,
        origin_location: str | None,
        origin_available: datetime,
    ) -> None:
        arrival_target = destination.start - timedelta(minutes=settings.padding_minutes)
        pairs.append(
            JourneyPairRef(
                journey_key=journey_key(
                    user_id=settings.user_id,
                    source_calendar_id=settings.source_calendar_id,
                    destination_occurrence_id=destination.occurrence_id,
                    mode=settings.mode,
                ),
                origin_occurrence_id=origin_occurrence_id,
                destination_occurrence_id=destination.occurrence_id,
                origin_location=origin_location,
                destination_location=destination.location,
                origin_available=origin_available,
                destination_start=destination.start,
                destination_arrival_target=arrival_target,
            )
        )

    physical = physical_events(events)
    if physical:
        first = physical[0]
        first_place = place_index.get(first.occurrence_id)
        first_available = first_origin_available(
            first_start=first.start,
            now=now,
            earliest_departure=settings.earliest_departure,
        )
        start_label = (
            settings.start_place.label if settings.start_place is not None else None
        )
        if first_place is None or settings.start_place is None or not places_match(
            settings.start_place, first_place
        ):
            add_pair("start_place", first, start_label, first_available)

        for previous, current in zip(physical, physical[1:], strict=False):
            add_pair(
                previous.occurrence_id,
                current,
                previous.location,
                previous.end,
            )

    for event in hybrid_events(events):
        add_pair(event.occurrence_id, event, event.location, event.start)

    for event in all_day_events(events):
        add_pair(event.occurrence_id, event, event.location, event.start)

    return pairs


def journey_key(
    *,
    user_id: str,
    source_calendar_id: str,
    destination_occurrence_id: str,
    mode: TravelMode,
) -> str:
    raw = "|".join(
        (user_id, source_calendar_id, destination_occurrence_id, mode.value)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_fingerprint(events: list[CalendarEvent]) -> str:
    """Stable revision of the source events one run depended on."""

    payload = [
        {
            "occurrence_id": event.occurrence_id,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "etag": event.etag,
            "location": event.location,
        }
        for event in sorted(events, key=lambda event: event.occurrence_id)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def block_hash(
    *,
    start: datetime,
    end: datetime,
    source_revision: str,
    padding_minutes: int,
) -> str:
    """Content hash identifying one applied travel block."""

    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source_revision": source_revision,
        "padding_minutes": padding_minutes,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
