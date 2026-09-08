"""Amazon Location Service Places and Routes V2 adapters.

Maps the ``geo-places`` ``SearchText`` and ``geo-routes`` ``CalculateRoutes``
operations onto Glide's provider contracts. Coordinates use the Amazon
Location V2 wire order ``[longitude, latitude]`` in both requests and
responses; ``PlaceRef`` normalizes them into separate fields.

Only scheduling-relevant state is retained from route responses: the summary
duration and the timing constraint. Route geometry, turn-by-turn steps, and
navigation details are read but never persisted. A place result is marked
``STORAGE_ALLOWED`` only when the caller requested ``IntendedUse=Storage``;
transient coordinate resolution for routing always uses ``SingleUse``.

Note: the exact exception the service uses for "no route" has not been pinned
against a live account yet. The adapter treats an empty ``Routes`` list or a
missing/invalid duration as ``NoRouteFoundError``, and also recognizes
route-unavailable wording inside a ``ValidationException``. Confirm the real
message during the first live routing test.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from glide.domain.models import (
    PlaceRef,
    RouteEstimate,
    StoragePolicyStatus,
    TimingConstraint,
    TravelMode,
)

MAX_CANDIDATES = 3
PLACE_ID_PREFIX = "amazon:"


class PlacesError(RuntimeError):
    """Base class for Amazon Location Places failures."""


class PlacesThrottledError(PlacesError):
    """The Places call was throttled and may be retried with backoff."""


class PlacesUnavailableError(PlacesError):
    """Places failed for a transient or internal reason."""


class PlacesAccessDeniedError(PlacesError):
    """The credentials do not allow the Places operation."""


class PlacesRequestError(PlacesError, ValueError):
    """The Places request itself was invalid."""


class RoutingError(RuntimeError):
    """Base class for Amazon Location Routes failures."""


class NoRouteFoundError(RoutingError):
    """No usable route exists between the two places."""


class RoutingThrottledError(RoutingError):
    """The Routes call was throttled and may be retried with backoff."""


class RoutingUnavailableError(RoutingError):
    """Routes failed for a transient or internal reason."""


class RoutingAccessDeniedError(RoutingError):
    """The credentials do not allow the Routes operation."""


class RoutingRequestError(RoutingError, ValueError):
    """The Routes request itself was invalid."""


def _error_code(exc: ClientError) -> str:
    response = exc.response or {}
    return str(response.get("Error", {}).get("Code", "UnknownError"))


def _error_message(exc: ClientError) -> str:
    response = exc.response or {}
    return str(response.get("Error", {}).get("Message", ""))


def _country_code(region: str) -> str:
    code = region.strip().upper()
    if re.fullmatch(r"[A-Z]{2,3}", code):
        return code
    raise PlacesRequestError(
        "region must be an ISO 3166 alpha-2 or alpha-3 country code, e.g. GB or GBR"
    )


def _raise_places_error(exc: ClientError) -> None:
    code = _error_code(exc)
    if code == "ThrottlingException":
        raise PlacesThrottledError(str(exc)) from exc
    if code == "AccessDeniedException":
        raise PlacesAccessDeniedError(str(exc)) from exc
    if code == "ValidationException":
        raise PlacesRequestError(str(exc)) from exc
    raise PlacesUnavailableError(str(exc)) from exc


def _no_route_message(message: str) -> bool:
    lowered = message.lower()
    return "route" in lowered and any(
        token in lowered for token in ("unable", "not found", "no route")
    )


def _raise_routing_error(exc: ClientError) -> None:
    code = _error_code(exc)
    if code == "ThrottlingException":
        raise RoutingThrottledError(str(exc)) from exc
    if code == "AccessDeniedException":
        raise RoutingAccessDeniedError(str(exc)) from exc
    if code == "ValidationException":
        if _no_route_message(_error_message(exc)):
            raise NoRouteFoundError(_error_message(exc)) from exc
        raise RoutingRequestError(str(exc)) from exc
    raise RoutingUnavailableError(str(exc)) from exc


def map_place_result(item: dict[str, Any], *, storage_allowed: bool) -> PlaceRef:
    """Map one ``SearchText`` result item onto a :class:`PlaceRef`."""

    place_id = item.get("PlaceId")
    if not place_id:
        raise PlacesUnavailableError("Amazon Location returned a place without a PlaceId")
    position = item.get("Position") or []
    longitude: float | None = None
    latitude: float | None = None
    if len(position) == 2:
        longitude, latitude = float(position[0]), float(position[1])
    address = item.get("Address") or {}
    label = item.get("Title") or address.get("Label") or "(unnamed place)"
    try:
        return PlaceRef(
            id=f"{PLACE_ID_PREFIX}{place_id}",
            provider_id=str(place_id),
            label=str(label),
            longitude=longitude,
            latitude=latitude,
            provenance="amazon-location-places",
            confirmed=False,
            storage_policy_status=(
                StoragePolicyStatus.STORAGE_ALLOWED
                if storage_allowed
                else StoragePolicyStatus.EPHEMERAL
            ),
        )
    except ValueError as exc:
        raise PlacesUnavailableError("Amazon Location returned a malformed place result") from exc


class AmazonLocationPlaces:
    """Places lookup backed by the ``geo-places`` ``SearchText`` operation."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def search(
        self,
        *,
        query: str,
        region: str | None = None,
        storage_allowed: bool = False,
    ) -> list[PlaceRef]:
        query = (query or "").strip()
        if not query:
            raise PlacesRequestError("search query must not be empty")
        params: dict[str, Any] = {
            "QueryText": query,
            "MaxResults": MAX_CANDIDATES,
            "IntendedUse": "Storage" if storage_allowed else "SingleUse",
        }
        if region:
            params["Filter"] = {"IncludeCountries": [_country_code(region)]}
        try:
            response = self._client.search_text(**params)
        except ClientError as exc:
            _raise_places_error(exc)
        return [
            map_place_result(item, storage_allowed=storage_allowed)
            for item in response.get("ResultItems", [])[:MAX_CANDIDATES]
        ]


def route_duration(response: dict[str, Any]) -> int:
    """Extract and validate the primary route duration in seconds."""

    routes = response.get("Routes") or []
    if not routes:
        raise NoRouteFoundError("Amazon Location returned no route")
    summary = routes[0].get("Summary") or {}
    duration = summary.get("Duration")
    if not isinstance(duration, int) or duration <= 0:
        raise NoRouteFoundError("Amazon Location returned a route without a valid duration")
    return duration


def _aws_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise RoutingRequestError("timing must be timezone-aware")
    return value.astimezone(UTC).isoformat()


class AmazonLocationRouter:
    """Driving estimates backed by ``geo-routes`` ``CalculateRoutes``.

    ``CalculateRoutes`` takes coordinates, not place IDs, so the router
    resolves each place through ``geo-places`` ``GetPlace`` with
    ``IntendedUse=SingleUse``. Resolved coordinates are cached for the
    lifetime of the adapter instance; nothing is written to storage.
    """

    def __init__(self, *, places_client: Any, routes_client: Any) -> None:
        self._places = places_client
        self._routes = routes_client
        self._coordinate_cache: dict[str, tuple[float, float]] = {}

    def estimate(
        self,
        *,
        origin_place_id: str,
        destination_place_id: str,
        mode: TravelMode,
        departure_at: datetime | None = None,
        arrival_by: datetime | None = None,
    ) -> RouteEstimate:
        if mode != TravelMode.DRIVING:
            raise RoutingRequestError(f"unsupported travel mode {mode}")
        if (departure_at is None) == (arrival_by is None):
            raise RoutingRequestError("exactly one of departure_at or arrival_by is required")

        origin = self._coordinates(origin_place_id)
        destination = self._coordinates(destination_place_id)
        timing_constraint = (
            TimingConstraint.ARRIVE_BY if arrival_by is not None else TimingConstraint.DEPART_AT
        )
        timing_time = arrival_by if arrival_by is not None else departure_at
        assert timing_time is not None

        params: dict[str, Any] = {
            "Origin": [origin[0], origin[1]],
            "Destination": [destination[0], destination[1]],
            "TravelMode": "Car",
            "LegGeometryFormat": "FlexiblePolyline",
            "OptimizeRoutingFor": "FastestRoute",
            "Traffic": {"Usage": "UseTrafficData"},
        }
        if arrival_by is not None:
            params["ArrivalTime"] = _aws_timestamp(arrival_by)
        else:
            params["DepartureTime"] = _aws_timestamp(departure_at)

        try:
            response = self._routes.calculate_routes(**params)
        except ClientError as exc:
            _raise_routing_error(exc)
        return RouteEstimate(
            id=f"amazon-route-{uuid.uuid4().hex[:12]}",
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
            mode=TravelMode.DRIVING,
            timing_constraint=timing_constraint,
            constraint_time=timing_time,
            duration_seconds=route_duration(response),
            provider="amazon-location",
            observed_at=datetime.now(UTC),
            quality="live",
            available=True,
        )

    def _coordinates(self, place_id: str) -> tuple[float, float]:
        cached = self._coordinate_cache.get(place_id)
        if cached is not None:
            return cached
        provider_id = place_id
        if provider_id.startswith(PLACE_ID_PREFIX):
            provider_id = provider_id[len(PLACE_ID_PREFIX) :]
        try:
            response = self._places.get_place(PlaceId=provider_id, IntendedUse="SingleUse")
        except ClientError as exc:
            _raise_places_error(exc)
        position = response.get("Position") or []
        if len(position) != 2:
            raise RoutingRequestError(f"no coordinates available for place {place_id}")
        longitude, latitude = float(position[0]), float(position[1])
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise RoutingRequestError(f"invalid coordinates for place {place_id}")
        pair = (longitude, latitude)
        self._coordinate_cache[place_id] = pair
        return pair
