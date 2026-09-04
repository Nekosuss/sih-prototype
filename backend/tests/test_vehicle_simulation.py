"""
Tests for Part 9 deterministic simulated vehicle movement
(app/simulation/vehicle_simulator.py). Every test drives advance_vehicle()
with an explicit `now` timestamp instead of real time.sleep() — the whole
point of this module is that the same elapsed time always produces the
same result, so tests assert exactly that rather than racing a real clock.

Synthetic graphs (prefixed local helpers, matching test_hazard_response.py's
convention) are used for scenarios needing a guaranteed configuration
(reroute, suspend, already-passed-segment safety); the real corridor
(`network`/`graph` fixtures) validates against genuine OSM geometry.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.geo import haversine_km
from app.core.hazard_state import build_hazard_event, combine_active_hazards_into_segment_context
from app.core.routing_engine import build_graph, calculate_route
from app.models.hazard import HazardSeverity, HazardType
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from app.models.vehicle import VehicleStatus
from app.simulation.vehicle_simulator import advance_vehicle, create_vehicle, pause_vehicle, reset_vehicle, start_vehicle

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _segment(
    seg_id, from_id, to_id, from_node, to_node, travel_time_min=10.0,
    slope_deg=0.5, historical_landslide_count=0, nearest_landslide_distance_m=None,
):
    # distance_km is ALWAYS the real haversine length of the geometry below
    # (exactly how osm_geojson_loader.py builds real segments) -- position
    # interpolation (core/geo.py::interpolate_along_path) walks this same
    # geometry, so a mismatched hand-picked distance_km would make the
    # vehicle's "declared distance travelled" and "real geometry position"
    # disagree, which a real segment can never do.
    distance_km = haversine_km(from_node.lat, from_node.lng, to_node.lat, to_node.lng)
    return RoadSegment(
        id=seg_id,
        from_node_id=from_id,
        to_node_id=to_id,
        road_type=RoadType.tertiary,
        distance_km=distance_km,
        estimated_travel_time_min=travel_time_min,
        geometry=[
            GeoPoint(lat=from_node.lat, lng=from_node.lng),
            GeoPoint(lat=to_node.lat, lng=to_node.lng),
        ],
        terrain_type=TerrainType.plain,
        slope_deg=slope_deg,
        elevation_m=500.0,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
        historical_landslide_count=historical_landslide_count,
        nearest_landslide_distance_m=nearest_landslide_distance_m,
    )


@pytest.fixture
def chain_network():
    """A-B-C-D, three 10km segments each 10 min at flat/no-history --
    predictable enough for exact position math."""
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.0, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=10.0, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=10.3, lng=10.0, type=NodeType.town)
    nodes = [a, b, c, d]
    seg_ab = _segment("seg_ab", "a", "b", a, b, travel_time_min=10)
    seg_bc = _segment("seg_bc", "b", "c", b, c, travel_time_min=10)
    seg_cd = _segment("seg_cd", "c", "d", c, d, travel_time_min=10)
    segments = [seg_ab, seg_bc, seg_cd]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


@pytest.fixture
def diamond_network():
    """A-B-D short/flat; A-C-D also flat -- for reroute-around-hazard tests."""
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.2, lng=10.2, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=10.1, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=10.4, lng=10.4, type=NodeType.town)
    nodes = [a, b, c, d]
    seg_ab = _segment("seg_ab", "a", "b", a, b, travel_time_min=10)
    seg_bd = _segment("seg_bd", "b", "d", b, d, travel_time_min=10)
    seg_ac = _segment("seg_ac", "a", "c", a, c, travel_time_min=11)
    seg_cd = _segment("seg_cd", "c", "d", c, d, travel_time_min=11)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


@pytest.fixture
def single_edge_network():
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.1, type=NodeType.town)
    nodes = [a, b]
    seg_ab = _segment("seg_ab", "a", "b", a, b, travel_time_min=10)
    segments = [seg_ab]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


# ---------------------------------------------------------------------------
# Vehicle model / creation
# ---------------------------------------------------------------------------


CHAIN_SEGMENT_KM = haversine_km(10.0, 10.0, 10.1, 10.0)  # each chain_network segment's real length


def test_create_vehicle_uses_real_route_and_starts_idle(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph)
    assert vehicle.status == VehicleStatus.idle
    assert vehicle.current_route.node_ids == ["a", "b", "c", "d"]
    assert vehicle.current_lat == pytest.approx(10.0)
    assert vehicle.current_lng == pytest.approx(10.0)
    assert vehicle.distance_remaining_km == pytest.approx(3 * CHAIN_SEGMENT_KM, abs=0.01)  # Route rounds to 2dp
    assert vehicle.progress == 0.0


def test_create_vehicle_fastest_mode_still_reports_route_risk(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph, mode="fastest")
    assert vehicle.route_risk is not None  # informational even though it didn't drive selection


def test_create_vehicle_unknown_location_raises(chain_network):
    from app.core.routing_engine import UnknownLocationError

    nodes, segments, graph = chain_network
    with pytest.raises(UnknownLocationError):
        create_vehicle("Truck 1", "Atlantis", "d", nodes, segments, graph)


# ---------------------------------------------------------------------------
# Route following: real geometry, not straight lines / random coordinates
# ---------------------------------------------------------------------------


def test_position_follows_real_route_geometry_not_straight_line(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph)
    vehicle = start_vehicle(vehicle)

    # After exactly 6 minutes at 60 km/h -> 6km travelled, still within
    # seg_ab (real length CHAIN_SEGMENT_KM ~11.1km).
    later = T0 + timedelta(minutes=6)
    vehicle.route_started_at = T0
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=later)

    assert vehicle.current_segment_id == "seg_ab"
    # a=(10.0,10.0), b=(10.1,10.0): 6km of CHAIN_SEGMENT_KM of the way there.
    fraction = 6.0 / CHAIN_SEGMENT_KM
    assert vehicle.current_lat == pytest.approx(10.0 + fraction * 0.1, abs=1e-6)
    assert vehicle.current_lng == pytest.approx(10.0, abs=1e-6)
    assert vehicle.distance_travelled_km == pytest.approx(6.0, abs=0.01)


def test_position_progresses_through_multiple_real_segments(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph)
    vehicle = start_vehicle(vehicle)
    vehicle.route_started_at = T0

    # 15 minutes at 60 km/h = 15km -> past seg_ab (~11.1km), into seg_bc.
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=15))
    assert vehicle.current_segment_id == "seg_bc"
    fraction = (15.0 - CHAIN_SEGMENT_KM) / CHAIN_SEGMENT_KM
    assert vehicle.current_lat == pytest.approx(10.1 + fraction * 0.1, abs=1e-6)
    assert vehicle.status == VehicleStatus.en_route


def test_vehicle_arrives_when_elapsed_distance_reaches_total(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph)
    vehicle = start_vehicle(vehicle)
    vehicle.route_started_at = T0

    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(hours=1))  # 60km >= 30km total
    assert vehicle.status == VehicleStatus.arrived
    assert vehicle.progress == 1.0
    assert vehicle.distance_remaining_km == 0.0
    assert vehicle.current_lat == pytest.approx(10.3, abs=1e-6)  # node d


# ---------------------------------------------------------------------------
# Deterministic movement
# ---------------------------------------------------------------------------


def test_same_elapsed_time_gives_identical_position(chain_network):
    nodes, segments, graph = chain_network
    v1 = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    v1.route_started_at = T0
    v2 = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    v2.route_started_at = T0

    later = T0 + timedelta(minutes=23)
    r1 = advance_vehicle(v1, nodes, segments, graph, now=later)
    r2 = advance_vehicle(v2, nodes, segments, graph, now=later)
    assert r1.current_lat == r2.current_lat
    assert r1.current_lng == r2.current_lng
    assert r1.distance_travelled_km == r2.distance_travelled_km


def test_no_speed_randomness_progress_is_linear_in_time(chain_network):
    nodes, segments, graph = chain_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    vehicle.route_started_at = T0

    at_10 = advance_vehicle(vehicle.model_copy(), nodes, segments, graph, now=T0 + timedelta(minutes=10))
    at_20 = advance_vehicle(vehicle.model_copy(), nodes, segments, graph, now=T0 + timedelta(minutes=20))
    assert at_20.distance_travelled_km == pytest.approx(2 * at_10.distance_travelled_km, rel=1e-6)


# ---------------------------------------------------------------------------
# Pause / resume / reset
# ---------------------------------------------------------------------------


def test_pause_freezes_position(chain_network):
    nodes, segments, graph = chain_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    vehicle.route_started_at = T0

    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=5))
    vehicle = pause_vehicle(vehicle)
    frozen_lat, frozen_lng = vehicle.current_lat, vehicle.current_lng

    # Advancing further while paused must not move it.
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=50))
    assert vehicle.current_lat == frozen_lat
    assert vehicle.current_lng == frozen_lng


def test_resume_continues_from_paused_position_not_from_start(chain_network):
    nodes, segments, graph = chain_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    vehicle.route_started_at = T0

    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=5))  # 5km in
    pause_time = T0 + timedelta(minutes=5)
    vehicle.paused = True
    vehicle.paused_since = pause_time

    # Paused for 20 minutes (no movement), then resumed.
    resume_time = pause_time + timedelta(minutes=20)
    vehicle = start_vehicle(vehicle)  # resumes -- but start_vehicle uses real now(), so set fields directly for determinism:
    vehicle.paused = False
    vehicle.paused_since = None
    vehicle.paused_seconds_total = 20 * 60  # 20 minutes paused, accounted for

    # 5 more minutes of driving after resuming.
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=resume_time + timedelta(minutes=5))
    assert vehicle.distance_travelled_km == pytest.approx(10.0, abs=0.01)  # 5km + 5km, the 20 paused minutes didn't count


def test_reset_returns_to_idle_with_zero_progress(chain_network):
    nodes, segments, graph = chain_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    vehicle.route_started_at = T0
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=20))
    assert vehicle.distance_travelled_km > 0

    vehicle = reset_vehicle(vehicle, nodes, segments, graph)
    assert vehicle.status == VehicleStatus.idle
    assert vehicle.progress == 0.0
    assert vehicle.distance_travelled_km == 0.0
    assert vehicle.route_started_at is None


def test_idle_vehicle_does_not_move_without_start(chain_network):
    nodes, segments, graph = chain_network
    vehicle = create_vehicle("Truck 1", "a", "d", nodes, segments, graph)
    advanced = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(hours=5))
    assert advanced.status == VehicleStatus.idle
    assert advanced.progress == 0.0


# ---------------------------------------------------------------------------
# Hazard awareness (Part 8 integration) -- reroute
# ---------------------------------------------------------------------------


def test_hazard_ahead_triggers_reroute_to_real_alternative(diamond_network):
    nodes, segments, graph = diamond_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    assert vehicle.current_route.node_ids == ["a", "b", "d"]
    vehicle.route_started_at = T0

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])

    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(seconds=1)
    )
    assert vehicle.status == VehicleStatus.rerouting
    assert vehicle.current_route.node_ids == ["a", "c", "d"]
    assert "seg_ab" not in vehicle.current_route.segment_ids
    assert vehicle.progress == 0.0  # simplification: restarts progress on the new route (documented)

    # Settles to en_route on the next tick.
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(seconds=2)
    )
    assert vehicle.status == VehicleStatus.en_route


def test_hazard_on_already_passed_segment_does_not_trigger_reroute(diamond_network):
    """Critical correctness case: build_remaining_route must exclude
    segments already safely driven -- a hazard behind the vehicle must
    never suspend/reroute it."""
    nodes, segments, graph = diamond_network
    seg_ab_km = haversine_km(10.0, 10.0, 10.2, 10.2)  # a -> b, real length
    minutes_per_km = 60.0 / 60.0  # speed_kmph default is 60 -> 1 min/km

    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    vehicle.route_started_at = T0

    # Drive most of the way through seg_ab (90% of its real length) first, with NO hazard.
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=0.9 * seg_ab_km * minutes_per_km))
    assert vehicle.current_segment_id == "seg_ab"
    assert vehicle.status == VehicleStatus.en_route

    # Now advance past the end of seg_ab and into seg_bd.
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=(seg_ab_km + 2.0) * minutes_per_km)
    )
    assert vehicle.current_segment_id == "seg_bd"

    # Hazarding the just-finished seg_ab (now BEHIND the vehicle) must not disrupt it.
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id],
        now=T0 + timedelta(minutes=(seg_ab_km + 3.0) * minutes_per_km),
    )
    assert vehicle.status == VehicleStatus.en_route  # unaffected -- seg_ab is behind it
    assert vehicle.current_route.node_ids == ["a", "b", "d"]  # route unchanged


# ---------------------------------------------------------------------------
# Hazard awareness -- suspend
# ---------------------------------------------------------------------------


def test_hazard_with_no_alternative_suspends_moving_vehicle(single_edge_network):
    nodes, segments, graph = single_edge_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "b", nodes, segments, graph))
    vehicle.route_started_at = T0

    vehicle = advance_vehicle(vehicle, nodes, segments, graph, now=T0 + timedelta(minutes=2))
    assert vehicle.status == VehicleStatus.en_route

    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(minutes=3)
    )
    assert vehicle.status == VehicleStatus.suspended
    assert vehicle.current_route.node_ids == ["a", "b"]  # no fabricated replacement
    # The vehicle kept moving right up to the tick where the hazard was
    # detected (3 minutes of real elapsed driving) -- THAT is the position
    # that must now be frozen, not an earlier snapshot.
    progress_at_suspend = vehicle.progress

    # Frozen: further advancement (even with the hazard still active) must not move it.
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(minutes=30)
    )
    assert vehicle.status == VehicleStatus.suspended
    assert vehicle.progress == progress_at_suspend


def test_suspended_vehicle_resumes_when_hazard_cleared(single_edge_network):
    nodes, segments, graph = single_edge_network
    vehicle = start_vehicle(create_vehicle("Truck 1", "a", "b", nodes, segments, graph))
    vehicle.route_started_at = T0

    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(minutes=1)
    )
    assert vehicle.status == VehicleStatus.suspended

    # Hazard cleared -- no active hazards passed in anymore.
    vehicle = advance_vehicle(vehicle, nodes, segments, graph, segment_context={}, active_hazard_ids=[], now=T0 + timedelta(minutes=2))
    assert vehicle.status in (VehicleStatus.en_route, VehicleStatus.rerouting)
    assert vehicle.current_route.node_ids == ["a", "b"]


# ---------------------------------------------------------------------------
# Multiple vehicles are independent
# ---------------------------------------------------------------------------


def test_multiple_vehicles_advance_independently(chain_network):
    nodes, segments, graph = chain_network
    v1 = start_vehicle(create_vehicle("Truck 1", "a", "d", nodes, segments, graph))
    v1.route_started_at = T0
    v2 = start_vehicle(create_vehicle("Truck 2", "a", "d", nodes, segments, graph))
    v2.route_started_at = T0 + timedelta(minutes=10)  # started later

    same_instant = T0 + timedelta(minutes=15)
    v1 = advance_vehicle(v1, nodes, segments, graph, now=same_instant)
    v2 = advance_vehicle(v2, nodes, segments, graph, now=same_instant)

    assert v1.distance_travelled_km > v2.distance_travelled_km  # v1 has been moving longer
    assert v1.id != v2.id


# ---------------------------------------------------------------------------
# Real-corridor integration
# ---------------------------------------------------------------------------


def test_create_vehicle_on_real_corridor(network, graph):
    nodes, segments = network
    vehicle = create_vehicle("Truck 1", "Guwahati", "Tawang", nodes, segments, graph)
    assert vehicle.current_route.total_distance_km > 0
    real_segment_ids = {s.id for s in segments}
    assert set(vehicle.current_route.segment_ids) <= real_segment_ids


def test_real_corridor_vehicle_reroutes_around_real_hazard(network, graph):
    nodes, segments = network
    vehicle = start_vehicle(create_vehicle("Truck 1", "Bhalukpong", "Bomdila", nodes, segments, graph))
    vehicle.route_started_at = T0

    doimara_ids = [sid for sid in vehicle.current_route.segment_ids
                   if segments[[s.id for s in segments].index(sid)].name == "Doimara-Nichiphu"]
    assert doimara_ids

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, doimara_ids)
    context = combine_active_hazards_into_segment_context([event])
    vehicle = advance_vehicle(
        vehicle, nodes, segments, graph, segment_context=context, active_hazard_ids=[event.id], now=T0 + timedelta(seconds=1)
    )
    assert vehicle.status == VehicleStatus.rerouting
    real_segment_ids = {s.id for s in segments}
    assert set(vehicle.current_route.segment_ids) <= real_segment_ids
    assert not (set(vehicle.current_route.segment_ids) & set(doimara_ids))
