"""Live AWS provider smoke: places, a real route, and one Strands run.

Requires ``AWS_PROFILE``/``AWS_REGION`` with Amazon Location access and
``BEDROCK_MODEL_ID`` with model access. It prints redacted evidence only; run
it after confirming a spending cap. The Strands run uses the fictional
fixture calendar with REAL providers, which must be labeled as such.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import boto3
from glide.adapters.amazon_location import AmazonLocationPlaces, AmazonLocationRouter
from glide.adapters.fixtures import (
    FixtureCalendar,
    canonical_settings,
    place_index,
)
from glide.agent.strands_runner import StrandsAgentRunner
from glide.domain.models import TravelMode, UserSettings


def main() -> int:
    region = os.environ["AWS_REGION"]
    model_id = os.environ["BEDROCK_MODEL_ID"]
    places_client = boto3.client("geo-places", region_name=region)
    routes_client = boto3.client("geo-routes", region_name=region)
    places = AmazonLocationPlaces(places_client)
    router = AmazonLocationRouter(
        places_client=places_client,
        routes_client=routes_client,
    )

    candidates = places.search(
        query=os.environ.get("GLIDE_PLACE_QUERY", "Big Ben, London"),
        region=os.environ.get("GLIDE_PLACE_REGION"),
        storage_allowed=False,
    )
    if len(candidates) < 2:
        raise SystemExit(f"live smoke needs two place candidates, got {len(candidates)}")
    print(
        "place:",
        candidates[0].label,
        f"({candidates[0].longitude}, {candidates[0].latitude})",
    )

    departure = datetime.now(UTC) + timedelta(hours=2)
    estimate = router.estimate(
        origin_place_id=candidates[0].id,
        destination_place_id=candidates[1].id,
        mode=TravelMode.DRIVING,
        departure_at=departure,
    )
    print(
        "route:",
        candidates[0].label,
        "->",
        candidates[1].label,
        f"{estimate.duration_seconds}s",
    )

    from strands.models.bedrock import BedrockModel

    day = date.today() + timedelta(days=1)
    calendar = FixtureCalendar(day=day)
    events = calendar.events()
    runner = StrandsAgentRunner(
        model=BedrockModel(model_id=model_id, region_name=region)
    )
    plans = runner.run(
        settings=UserSettings.model_validate(canonical_settings()),
        events=events,
        place_index=place_index(events),
        router=router,
        now=datetime.now(UTC),
    )
    for plan in plans:
        print("plan:", plan.action.value, plan.reason_code, plan.destination_occurrence_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
