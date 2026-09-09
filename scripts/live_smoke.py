"""Live AWS provider smoke: two real places, a real route, one Strands run.

Requires ``AWS_REGION`` with Amazon Location access and ``BEDROCK_MODEL_ID``
with model access. It prints redacted evidence only; run it after confirming
a spending cap.

The schedule fed to the agent is synthetic, but every place reference, route,
and model call below uses real provider identifiers and outcomes. The origin
and destination venues are resolved by two independent searches so the
fictional fixture ids are never passed to a real router.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import boto3
from glide.adapters.amazon_location import AmazonLocationPlaces, AmazonLocationRouter
from glide.adapters.fixtures import canonical_settings
from glide.agent.strands_runner import StrandsAgentRunner
from glide.domain.models import (
    CalendarEvent,
    EventKind,
    TravelMode,
    UserSettings,
)


def _resolve(places: AmazonLocationPlaces, env: str, default: str):
    candidates = places.search(
        query=os.environ.get(env, default),
        region=os.environ.get("GLIDE_PLACE_REGION"),
        storage_allowed=False,
    )
    if not candidates:
        raise SystemExit(f"no place found for {env} query")
    place = candidates[0].model_copy(update={"confirmed": True})
    print(
        "place:",
        place.label,
        f"({place.longitude}, {place.latitude})",
        "confirmed=True",
    )
    return place


def _event(
    occurrence_id: str,
    place,
    start: datetime,
    end: datetime,
) -> CalendarEvent:
    return CalendarEvent(
        provider_event_id=f"smoke-{occurrence_id}",
        occurrence_id=occurrence_id,
        calendar_id="smoke-calendar",
        etag=f"etag-{occurrence_id}",
        start=start,
        end=end,
        original_time_zone="Europe/London",
        title=f"Visit {place.label}",
        location=place.label,
        place_id=place.id,
        kind=EventKind.PHYSICAL,
    )


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

    origin = _resolve(places, "GLIDE_PLACE_QUERY_ORIGIN", "Big Ben, London")
    destination = _resolve(
        places,
        "GLIDE_PLACE_QUERY_DESTINATION",
        "The Shard, London",
    )

    departure = datetime.now(UTC) + timedelta(hours=2)
    estimate = router.estimate(
        origin_place_id=origin.id,
        destination_place_id=destination.id,
        mode=TravelMode.DRIVING,
        departure_at=departure,
    )
    print(
        "route:",
        origin.label,
        "->",
        destination.label,
        f"{estimate.duration_seconds}s",
        f"quality={estimate.quality}",
    )

    from strands.models.bedrock import BedrockModel

    morning = datetime.now(UTC) + timedelta(hours=4)
    afternoon = morning + timedelta(hours=3)
    origin_event = _event("origin", origin, morning, morning + timedelta(hours=1))
    destination_event = _event(
        "destination",
        destination,
        afternoon,
        afternoon + timedelta(hours=1),
    )
    events = [origin_event, destination_event]
    settings = UserSettings.model_validate(
        {
            **canonical_settings(),
            "start_place": origin.model_dump(mode="json"),
            "time_zone": "Europe/London",
        }
    )
    runner = StrandsAgentRunner(
        model=BedrockModel(model_id=model_id, region_name=region)
    )
    plans = runner.run(
        settings=settings,
        events=events,
        place_index={
            "origin": origin,
            "destination": destination,
        },
        router=router,
        now=datetime.now(UTC),
    )
    for plan in plans:
        print(
            "plan:",
            plan.action.value,
            plan.reason_code,
            plan.destination_occurrence_id,
        )
    print("schedule=synthetic places=routes=model=live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
