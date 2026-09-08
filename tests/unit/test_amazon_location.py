from __future__ import annotations

from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError
from glide.adapters.amazon_location import (
    AmazonLocationPlaces,
    AmazonLocationRouter,
    NoRouteFoundError,
    PlacesAccessDeniedError,
    PlacesRequestError,
    PlacesThrottledError,
    PlacesUnavailableError,
    RoutingAccessDeniedError,
    RoutingRequestError,
    RoutingThrottledError,
    RoutingUnavailableError,
)
from glide.domain.models import StoragePolicyStatus, TimingConstraint, TravelMode


def client_error(code: str, message: str = "provider error") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "OperationName",
    )


class FakePlacesClient:
    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.get_place_calls: list[dict] = []
        self.search_results: list[dict] = []
        self.search_error: ClientError | None = None
        self.place_positions: dict[str, list[float]] = {}
        self.place_details: dict[str, dict] = {}
        self.get_place_error: ClientError | None = None

    def search_text(self, **kwargs):
        self.search_calls.append(kwargs)
        if self.search_error is not None:
            raise self.search_error
        return {"ResultItems": list(self.search_results)}

    def get_place(self, **kwargs):
        self.get_place_calls.append(kwargs)
        if self.get_place_error is not None:
            raise self.get_place_error
        if kwargs["PlaceId"] in self.place_details:
            return self.place_details[kwargs["PlaceId"]]
        position = self.place_positions.get(kwargs["PlaceId"])
        if position is None:
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "unknown place"}},
                "GetPlace",
            )
        return {"Position": position}


class FakeRoutesClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response: dict = {"Routes": [{"Summary": {"Duration": 1500, "Distance": 9000}}]}
        self.error: ClientError | None = None

    def calculate_routes(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def search_result(
    *,
    place_id: str = "here:pds:place:123",
    title: str = "Northside Community Centre",
    label: str = "1 Example Street",
    position: list[float] | None = None,
) -> dict:
    return {
        "PlaceId": place_id,
        "PlaceType": "PointOfInterest",
        "Title": title,
        "Address": {"Label": label},
        "Position": position if position is not None else [-0.1276, 51.5072],
    }


def test_search_maps_candidates_with_storage_policy() -> None:
    client = FakePlacesClient()
    client.search_results = [search_result()]
    places = AmazonLocationPlaces(client)

    ephemeral = places.search(query="northside", region=None, storage_allowed=False)
    stored = places.search(query="northside", region=None, storage_allowed=True)

    assert len(ephemeral) == 1
    first = ephemeral[0]
    assert first.id == "amazon:here:pds:place:123"
    assert first.provider_id == "here:pds:place:123"
    assert first.label == "Northside Community Centre"
    assert first.longitude == -0.1276
    assert first.latitude == 51.5072
    assert first.provenance == "amazon-location-places"
    assert first.confirmed is False
    assert first.storage_policy_status is StoragePolicyStatus.EPHEMERAL
    assert stored[0].storage_policy_status is StoragePolicyStatus.STORAGE_ALLOWED
    assert client.search_calls[0]["IntendedUse"] == "SingleUse"
    assert client.search_calls[1]["IntendedUse"] == "Storage"
    assert client.search_calls[0]["QueryText"] == "northside"
    assert client.search_calls[0]["MaxResults"] == 3


def test_search_uses_iso_country_filter_for_region() -> None:
    client = FakePlacesClient()
    places = AmazonLocationPlaces(client)

    places.search(query="westfield", region="gb")
    places.search(query="westfield", region="GBR")

    assert client.search_calls[0]["Filter"] == {"IncludeCountries": ["GB"]}
    assert client.search_calls[1]["Filter"] == {"IncludeCountries": ["GBR"]}


def test_search_rejects_free_text_region() -> None:
    places = AmazonLocationPlaces(FakePlacesClient())
    with pytest.raises(PlacesRequestError, match="ISO 3166"):
        places.search(query="westfield", region="London")


def test_search_rejects_empty_query() -> None:
    places = AmazonLocationPlaces(FakePlacesClient())
    with pytest.raises(PlacesRequestError, match="must not be empty"):
        places.search(query="   ")


def test_search_caps_candidates_at_three() -> None:
    client = FakePlacesClient()
    client.search_results = [
        search_result(place_id=f"place-{index}") for index in range(5)
    ]
    places = AmazonLocationPlaces(client)

    results = places.search(query="common name")

    assert len(results) == 3
    assert [result.provider_id for result in results] == ["place-0", "place-1", "place-2"]


def test_search_maps_provider_errors() -> None:
    client = FakePlacesClient()
    places = AmazonLocationPlaces(client)

    client.search_error = client_error("ThrottlingException")
    with pytest.raises(PlacesThrottledError):
        places.search(query="x")
    client.search_error = client_error("AccessDeniedException")
    with pytest.raises(PlacesAccessDeniedError):
        places.search(query="x")
    client.search_error = client_error("ValidationException")
    with pytest.raises(PlacesRequestError):
        places.search(query="x")
    client.search_error = client_error("InternalServerException")
    with pytest.raises(PlacesUnavailableError):
        places.search(query="x")


def test_search_malformed_result_without_place_id() -> None:
    client = FakePlacesClient()
    client.search_results = [{"Title": "no id"}]
    places = AmazonLocationPlaces(client)
    with pytest.raises(PlacesUnavailableError, match="without a PlaceId"):
        places.search(query="x")


def test_route_estimate_uses_arrival_time_and_car_mode() -> None:
    places = FakePlacesClient()
    places.place_positions = {
        "origin": [-0.12, 51.50],
        "destination": [-0.13, 51.51],
    }
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)
    arrival_by = datetime(2026, 9, 9, 10, 30, tzinfo=UTC)

    estimate = router.estimate(
        origin_place_id="origin",
        destination_place_id="destination",
        mode=TravelMode.DRIVING,
        arrival_by=arrival_by,
    )

    assert estimate.duration_seconds == 1500
    assert estimate.timing_constraint is TimingConstraint.ARRIVE_BY
    assert estimate.constraint_time == arrival_by
    assert estimate.provider == "amazon-location"
    assert estimate.quality == "live"
    assert estimate.available is True
    assert "geometry" not in estimate.model_dump()

    request = routes.calls[0]
    assert request["Origin"] == [-0.12, 51.50]
    assert request["Destination"] == [-0.13, 51.51]
    assert request["TravelMode"] == "Car"
    assert request["ArrivalTime"] == "2026-09-09T10:30:00+00:00"
    assert "DepartureTime" not in request
    assert request["Traffic"] == {"Usage": "UseTrafficData"}
    assert request["LegGeometryFormat"] == "FlexiblePolyline"
    assert request["OptimizeRoutingFor"] == "FastestRoute"


def test_route_estimate_uses_departure_time() -> None:
    places = FakePlacesClient()
    places.place_positions = {"a": [0.0, 0.0], "b": [0.1, 0.1]}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)
    departure_at = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)

    estimate = router.estimate(
        origin_place_id="a",
        destination_place_id="b",
        mode=TravelMode.DRIVING,
        departure_at=departure_at,
    )

    assert estimate.timing_constraint is TimingConstraint.DEPART_AT
    request = routes.calls[0]
    assert request["DepartureTime"] == "2026-09-09T09:00:00+00:00"
    assert "ArrivalTime" not in request


def test_route_maps_no_route_responses() -> None:
    places = FakePlacesClient()
    places.place_positions = {"a": [0.0, 0.0], "b": [0.1, 0.1]}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)

    routes.response = {"Routes": []}
    with pytest.raises(NoRouteFoundError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )

    routes.response = {"Routes": [{"Summary": {"Duration": 0}}]}
    with pytest.raises(NoRouteFoundError, match="valid duration"):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )


def test_route_treats_no_route_validation_message_as_not_found() -> None:
    places = FakePlacesClient()
    places.place_positions = {"a": [0.0, 0.0], "b": [0.1, 0.1]}
    routes = FakeRoutesClient()
    routes.error = client_error(
        "ValidationException",
        "Unable to find a route between the origin and destination",
    )
    router = AmazonLocationRouter(places_client=places, routes_client=routes)

    with pytest.raises(NoRouteFoundError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )


def test_route_maps_other_provider_errors() -> None:
    places = FakePlacesClient()
    places.place_positions = {"a": [0.0, 0.0], "b": [0.1, 0.1]}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)
    departure_at = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)

    routes.error = client_error("ValidationException", "invalid timestamp")
    with pytest.raises(RoutingRequestError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=departure_at,
        )
    routes.error = client_error("ThrottlingException")
    with pytest.raises(RoutingThrottledError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=departure_at,
        )
    routes.error = client_error("AccessDeniedException")
    with pytest.raises(RoutingAccessDeniedError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=departure_at,
        )
    routes.error = client_error("InternalServerException")
    with pytest.raises(RoutingUnavailableError):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=departure_at,
        )


def test_route_rejects_unsupported_mode_and_ambiguous_timing() -> None:
    places = FakePlacesClient()
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)
    departure_at = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    arrival_by = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)

    with pytest.raises(RoutingRequestError, match="exactly one"):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
        )
    with pytest.raises(RoutingRequestError, match="exactly one"):
        router.estimate(
            origin_place_id="a",
            destination_place_id="b",
            mode=TravelMode.DRIVING,
            departure_at=departure_at,
            arrival_by=arrival_by,
        )
    assert routes.calls == []


def test_route_resolves_prefixed_place_ids_and_caches_coordinates() -> None:
    places = FakePlacesClient()
    places.place_positions = {"p1": [-0.1, 51.5], "p2": [-0.2, 51.6]}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)
    departure_at = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)

    router.estimate(
        origin_place_id="amazon:p1",
        destination_place_id="amazon:p2",
        mode=TravelMode.DRIVING,
        departure_at=departure_at,
    )
    router.estimate(
        origin_place_id="amazon:p1",
        destination_place_id="amazon:p2",
        mode=TravelMode.DRIVING,
        departure_at=departure_at,
    )

    assert places.get_place_calls == [
        {"PlaceId": "p1", "IntendedUse": "SingleUse"},
        {"PlaceId": "p2", "IntendedUse": "SingleUse"},
    ]
    assert len(routes.calls) == 2


def test_route_fails_without_place_coordinates() -> None:
    places = FakePlacesClient()
    places.place_details = {"missing": {"PlaceId": "missing"}}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)

    with pytest.raises(RoutingRequestError, match="no coordinates"):
        router.estimate(
            origin_place_id="missing",
            destination_place_id="missing",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )
    assert routes.calls == []


def test_route_unknown_place_id_is_a_places_request_error() -> None:
    places = FakePlacesClient()
    places.place_positions = {"known": [0.0, 0.0]}
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)

    with pytest.raises(PlacesRequestError):
        router.estimate(
            origin_place_id="known",
            destination_place_id="unknown",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )
    assert routes.calls == []


def test_route_propagates_places_failures_during_resolution() -> None:
    places = FakePlacesClient()
    places.place_positions = {"ok": [0.0, 0.0]}
    places.get_place_error = client_error("ThrottlingException")
    routes = FakeRoutesClient()
    router = AmazonLocationRouter(places_client=places, routes_client=routes)

    with pytest.raises(PlacesThrottledError):
        router.estimate(
            origin_place_id="ok",
            destination_place_id="blocked",
            mode=TravelMode.DRIVING,
            departure_at=datetime(2026, 9, 9, 9, 0, tzinfo=UTC),
        )
    assert routes.calls == []
