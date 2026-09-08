"""Ten consecutive canonical integrated runs with latency evidence.

Each run is a fresh isolated sample tenant using the deterministic runner,
fixture calendar, and fixture routes — no live providers. The script fails if
any run deviates from the canonical one-block plus one-decision outcome.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

from glide.adapters.fixtures import (
    FixtureCalendar,
    FixtureRouter,
    canonical_settings,
    place_index,
)
from glide.domain.engine import InMemoryTravelCalendar, SampleWorkflow
from glide.domain.models import UserSettings


def main() -> int:
    day = date.today() + timedelta(days=1)
    now = datetime.now(UTC)
    elapsed: list[float] = []

    for index in range(10):
        settings = UserSettings.model_validate(
            canonical_settings(user_id=f"ten-run-{index}")
        )
        calendar = FixtureCalendar(day=day)
        workflow = SampleWorkflow(
            settings=settings,
            travel_calendar=InMemoryTravelCalendar(),
            router=FixtureRouter(),
        )
        started = time.perf_counter()
        result = workflow.run(
            source_events=calendar.events(),
            place_index=place_index(calendar.events()),
            now=now,
            run_id=f"ten-run-{index}",
        )
        elapsed.append(time.perf_counter() - started)
        if len(result.travel_blocks) != 1 or len(result.decisions) != 1:
            raise SystemExit(
                f"run {index + 1} deviated: blocks={len(result.travel_blocks)} "
                f"decisions={len(result.decisions)}"
            )
        print(
            f"run {index + 1}: {elapsed[-1] * 1000:7.1f} ms  "
            f"status={result.run.status.value} blocks=1 decisions=1"
        )

    mean = sum(elapsed) / len(elapsed)
    print(f"mean {mean * 1000:.1f} ms over {len(elapsed)} runs")
    print("providers: fixture calendar, fixture routes, deterministic runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
