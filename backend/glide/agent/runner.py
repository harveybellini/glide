"""Run-processor boundary.

``StrandsAgentRunner`` will be the production implementation once Bedrock
access is available. ``DeterministicAgentRunner`` exists so the scheduling loop
can be exercised offline and gives the live integration a clear behavioural
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from glide.domain.models import (
    CalendarEvent,
    JourneyPlan,
    PlaceRef,
    UserSettings,
)
from glide.domain.scheduling import RouteEstimator, build_journey_plans


class AgentRunner(Protocol):
    def run(
        self,
        *,
        settings: UserSettings,
        events: list[CalendarEvent],
        place_index: dict[str, PlaceRef],
        router: RouteEstimator,
        now: datetime,
    ) -> list[JourneyPlan]: ...


@dataclass(frozen=True)
class DeterministicAgentRunner:
    """Offline runner that delegates to the deterministic planner."""

    def run(
        self,
        *,
        settings: UserSettings,
        events: list[CalendarEvent],
        place_index: dict[str, PlaceRef],
        router: RouteEstimator,
        now: datetime,
    ) -> list[JourneyPlan]:
        return build_journey_plans(
            settings=settings,
            events=events,
            place_index=place_index,
            estimator=router,
            now=now,
        )
