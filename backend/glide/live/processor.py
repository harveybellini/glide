"""Live maintenance run processor: the deployed counterpart of the sample
processor.

For one queued job it reloads settings, re-reads the source calendar and the
owned blocks, resolves place text through the place provider, runs the agent
and executor, and persists the complete result atomically. Provider adapters
are injected so the whole pipeline is testable without credentials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from glide.adapters.interfaces import CalendarAdapter, PlaceLookup, StateStore
from glide.agent.runner import AgentRunner, DeterministicAgentRunner
from glide.domain.decisions import close_stale_decisions
from glide.domain.live import LiveWorkflow
from glide.domain.models import PlaceRef, UserSettings
from glide.domain.scheduling import RouteEstimator
from glide.jobs.queue import Job


@dataclass
class LiveRunProcessor:
    state_store: StateStore
    calendar_factory: Callable[[UserSettings], CalendarAdapter]
    router_factory: Callable[[UserSettings], RouteEstimator]
    place_search: PlaceLookup
    runner: AgentRunner
    window_seconds: int = 48 * 3600
    lookback_seconds: int = 3600

    def process(self, job: Job) -> None:
        settings = self.state_store.get_settings(job.user_id)
        if settings is None:
            raise RuntimeError(f"no persisted settings for user {job.user_id}")

        now = datetime.now(UTC)
        calendar = self.calendar_factory(settings)
        glide_calendar_id = calendar.ensure_travel_calendar(
            settings.glide_calendar_id
        )
        window_start = now - timedelta(seconds=self.lookback_seconds)
        window_end = now + timedelta(seconds=self.window_seconds)

        source_events = calendar.list_events(
            calendar_id=settings.source_calendar_id,
            window_start=window_start,
            window_end=window_end,
        )
        place_index = self._resolve_places(source_events)
        previous_blocks = self.state_store.get_blocks(settings.user_id)

        workflow = LiveWorkflow(
            settings=settings,
            calendar=calendar,
            glide_calendar_id=glide_calendar_id,
            router=self.router_factory(settings),
            runner=self.runner,
        )
        result = workflow.run(
            source_events=source_events,
            place_index=place_index,
            now=now,
            window_start=window_start,
            window_end=window_end,
            previous_blocks=previous_blocks,
            run_id=job.run_id,
        )
        close_stale_decisions(self.state_store, result)
        self.state_store.save_result(result)

    def _resolve_places(self, events) -> dict[str, PlaceRef]:
        resolved: dict[str, PlaceRef] = {}
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
                cache[text] = candidates[0] if len(candidates) == 1 else None
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
    window_seconds: int = 48 * 3600,
    lookback_seconds: int = 3600,
) -> LiveRunProcessor:
    return LiveRunProcessor(
        state_store=state_store,
        calendar_factory=calendar_factory,
        router_factory=router_factory,
        place_search=place_search,
        runner=runner or DeterministicAgentRunner(),
        window_seconds=window_seconds,
        lookback_seconds=lookback_seconds,
    )
