"""Live maintenance run processor: the deployed counterpart of the sample
processor.

For one queued job it reloads settings, re-reads the source calendar and the
owned blocks, resolves place text through the place provider, runs the agent
and executor, and persists the complete result atomically. Provider adapters
are injected so the whole pipeline is testable without credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from glide.adapters.interfaces import (
    CalendarAdapter,
    DecisionNotifier,
    PlaceLookup,
    StateStore,
)
from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.domain.decisions import ADD_ANYWAY, close_stale_decisions
from glide.domain.live import LiveWorkflow, SettingsChangedError
from glide.domain.models import DecisionStatus, PlaceRef, UserSettings
from glide.domain.notifications import (
    carry_notification_state,
    deliver_open_decisions,
)
from glide.domain.scheduling import RouteEstimator, source_fingerprint
from glide.jobs.queue import Job

MANAGED_CALENDAR_ID = "primary"

# Amazon Location returns several records for one real place: a terminal and its
# arrivals hall 147 m apart, a building and the gallery inside it 0 m apart.
# Candidates that all sit within this radius of the top match are one place.
# Genuinely different candidates - three Costa branches 388 m apart in the City,
# a hotel 6 km from the airport that shares its name - stay a user decision.
NEAR_DUPLICATE_METRES = 200.0


def choose_place_match(candidates: list[PlaceRef]) -> PlaceRef | None:
    """Return the provider's match, tolerating near-duplicate records.

    The planner only needs to know where the appointment is. A single candidate
    is taken as-is; several candidates are accepted only when they are the same
    place to within ``NEAR_DUPLICATE_METRES``, which is where the top match is
    the resolution. Anything more spread out, or without coordinates, is a
    genuine choice and returns ``None`` so the run raises a decision instead of
    guessing.
    """

    if not candidates:
        return None
    top = candidates[0]
    if len(candidates) == 1:
        return top
    if top.longitude is None or top.latitude is None:
        return None
    for other in candidates[1:]:
        if other.longitude is None or other.latitude is None:
            return None
        if _metres_between(top, other) > NEAR_DUPLICATE_METRES:
            return None
    return top


def _metres_between(first: PlaceRef, second: PlaceRef) -> float:
    """Approximate distance in metres; the radius is hundreds of metres."""

    import math

    assert first.longitude is not None and first.latitude is not None
    assert second.longitude is not None and second.latitude is not None
    mean_lat = math.radians((first.latitude + second.latitude) / 2)
    dx = (second.longitude - first.longitude) * 111_320 * math.cos(mean_lat)
    dy = (second.latitude - first.latitude) * 110_540
    return math.hypot(dx, dy)


@dataclass
class LiveRunProcessor:
    state_store: StateStore
    calendar_factory: Callable[[UserSettings], CalendarAdapter]
    router_factory: Callable[[UserSettings], RouteEstimator]
    place_search: PlaceLookup
    runner: AgentRunner
    # Optional so the sample path and offline tests never send messages; the
    # deployed worker injects the SES adapter for live tenants.
    notifier: DecisionNotifier | None = None
    window_seconds: int = 48 * 3600
    lookback_seconds: int = 3600
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(UTC),
        repr=False,
    )

    def process(self, job: Job) -> None:
        settings = self.state_store.get_settings(job.user_id)
        if settings is None:
            raise RuntimeError(f"no persisted settings for user {job.user_id}")

        now = self.clock()
        calendar = self.calendar_factory(settings)
        if settings.glide_calendar_id != MANAGED_CALENDAR_ID:
            legacy_id = settings.glide_calendar_id or settings.legacy_glide_calendar_id
            settings = settings.model_copy(
                update={
                    "glide_calendar_id": MANAGED_CALENDAR_ID,
                    "legacy_glide_calendar_id": legacy_id or None,
                    "revision": settings.revision + 1,
                }
            )
            self.state_store.save_settings(settings)
        window_start = now - timedelta(seconds=self.lookback_seconds)
        window_end = now + timedelta(seconds=self.window_seconds)

        source_events = calendar.list_events(
            calendar_id=settings.source_calendar_id,
            window_start=window_start,
            window_end=window_end,
        )
        place_index = self._resolve_places(
            source_events,
            settings.location_overrides,
        )
        previous_blocks = self.state_store.get_blocks(settings.user_id)
        fingerprint = source_fingerprint(source_events)
        decisions = self.state_store.get_decisions(settings.user_id)
        manual_deletions = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.OPEN
            and decision.reason == "manually_deleted"
        }
        # A resolved skip is honored while the source revision it was made
        # against is unchanged; a source edit produces a new revision and the
        # journey may be reconsidered.
        skip_journeys = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.RESOLVED
            and decision.resolution
            in {"skip_journey", "treat_as_virtual", "keep_manual_edit"}
            and decision.source_revision == fingerprint
        }
        # An accepted "Add it anyway" is honored on the same terms: it keeps
        # adding the block the arithmetic refused while the source revision it
        # was accepted against is unchanged.
        force_journeys = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.RESOLVED
            and decision.resolution == ADD_ANYWAY
            and decision.source_revision == fingerprint
        }
        accepted_manual = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.RESOLVED
            and decision.resolution == "keep_manual_edit"
            and decision.source_revision == fingerprint
        }
        replace_journeys = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.RESOLVED
            and decision.resolution == "replace_with_plan"
            and decision.source_revision == fingerprint
        }
        recreate_journeys = {
            decision.journey_key
            for decision in decisions
            if decision.status == DecisionStatus.RESOLVED
            and decision.resolution == "recreate_journey"
            and decision.source_revision == fingerprint
        }

        workflow = LiveWorkflow(
            settings=settings,
            calendar=calendar,
            glide_calendar_id=MANAGED_CALENDAR_ID,
            router=self.router_factory(settings),
            runner=self.runner,
            mutation_guard=lambda: self._assert_current(settings),
        )
        result = workflow.run(
            trigger=job.trigger or "live",
            source_events=source_events,
            place_index=place_index,
            now=now,
            window_start=window_start,
            window_end=window_end,
            previous_blocks=previous_blocks,
            manual_deletions=manual_deletions,
            skip_journeys=skip_journeys,
            force_journeys=force_journeys,
            accepted_manual=accepted_manual,
            replace_journeys=replace_journeys,
            recreate_journeys=recreate_journeys,
            run_id=job.run_id,
        )
        current = self.state_store.get_settings(settings.user_id)
        if current is not None and current.revision != settings.revision:
            # The user changed settings while the external writes ran; the
            # result reflects a superseded policy and must not be committed.
            raise SettingsChangedError(
                "settings changed during the run; requeueing for a fresh policy"
            )
        close_stale_decisions(self.state_store, result)
        if self.notifier is not None:
            # Keep the persisted once-only mark on decisions this run rebuilt
            # from the calendar, or the next poll would notify again.
            result = carry_notification_state(self.state_store, result)
        self.state_store.save_result(result)
        if self.notifier is not None:
            deliver_open_decisions(self.state_store, self.notifier, result)

    def _assert_current(self, expected: UserSettings) -> None:
        current = self.state_store.get_settings(expected.user_id)
        if (
            current is None
            or not current.enabled
            or current.revision != expected.revision
        ):
            raise SettingsChangedError(
                "settings changed or automation paused during the run"
            )

    def _resolve_places(
        self,
        events,
        overrides: dict[str, PlaceRef] | None = None,
    ) -> dict[str, PlaceRef]:
        resolved: dict[str, PlaceRef] = dict(overrides or {})
        cache: dict[str, PlaceRef | None] = {}
        for event in events:
            text = (event.location or "").strip()
            if not text:
                continue
            if event.occurrence_id in resolved:
                continue
            if text not in cache:
                candidates = self.place_search.search(
                    query=text,
                    storage_allowed=False,
                )
                cache[text] = choose_place_match(candidates)
            place = cache[text]
            if place is not None:
                resolved[event.occurrence_id] = place
        return resolved


def build_live_processor(
    *,
    state_store: StateStore,
    calendar_factory: Callable[[UserSettings], CalendarAdapter],
    router_factory: Callable[[UserSettings], RouteEstimator],
    place_search: PlaceLookup,
    runner: AgentRunner | None = None,
    notifier: DecisionNotifier | None = None,
    window_seconds: int = 48 * 3600,
    lookback_seconds: int = 3600,
    clock: Callable[[], datetime] | None = None,
) -> LiveRunProcessor:
    return LiveRunProcessor(
        state_store=state_store,
        calendar_factory=calendar_factory,
        router_factory=router_factory,
        place_search=place_search,
        runner=runner or DeterministicAgentRunner(),
        notifier=notifier,
        window_seconds=window_seconds,
        lookback_seconds=lookback_seconds,
        clock=clock or (lambda: datetime.now(UTC)),
    )
