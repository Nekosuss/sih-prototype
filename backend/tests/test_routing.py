"""
Tests for the routing engine (core/routing_engine.py) against the real OSM
GeoJSON road network.

Exercises routing_engine.calculate_route() directly — no FastAPI/HTTP
involved, per the design principle that the engine must be usable as plain
Python. API-level tests (POST /routes/calculate) live in test_api.py.

Real node ids are generated from coordinates (see osm_geojson_loader.py),
not hand-picked slugs — tests resolve the 7 demonstration locations by name
via resolve_location(), exactly as a real caller would.

A small synthetic diamond-shaped graph (built only in this file, never
touching the real dataset) is used for the one test that needs a
controlled, guaranteed cost/distance mismatch: proving the engine picks the
lowest-travel-time path rather than the lowest-distance one.
"""
import networkx as nx
import pytest

from app.core.geo import haversine_km
from app.core.routing_engine import (
    NoRouteFoundError,
    UnknownLocationError,
    build_graph,
    calculate_route,
    has_alternative_path,
    resolve_location,
)
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from tests.conftest import CORRIDOR_ORDER


def town_id(nodes, name):
    return resolve_location(nodes, name).id


# ---------------------------------------------------------------------------
# Synthetic diamond graph: A -> B -> D (short distance, slow) and
# A -> C -> D (long distance, fast). Built only for these tests — never added
# to the real dataset.
# ---------------------------------------------------------------------------

def _segment(seg_id, from_id, to_id, from_node, to_node, distance_km, travel_time_min):
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
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
    )


@pytest.fixture(scope="module")
def synthetic_network():
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.4, lng=10.4, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=10.1, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=11.0, lng=11.0, type=NodeType.town)
    nodes = [a, b, c, d]

    # Short distance, slow (unpaved/mountain-style): 20 km total, 400 min total
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=10, travel_time_min=200)
    seg_bd = _segment("seg_bd", "b", "d", b, d, distance_km=10, travel_time_min=200)
    # Long distance, fast (highway-style): 100 km total, 80 min total
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=50, travel_time_min=40)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=50, travel_time_min=40)

    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    return nodes, segments


@pytest.fixture(scope="module")
def synthetic_graph(synthetic_network):
    nodes, segments = synthetic_network
    return build_graph(nodes, segments)


def test_synthetic_route_picks_fastest_not_shortest(synthetic_network, synthetic_graph):
    """Proves the cost function is travel time, not distance: the A-B-D leg
    is shorter in distance (20km) but far slower (400min) than A-C-D
    (100km, 80min). calculate_route must choose A-C-D."""
    nodes, segments = synthetic_network
    route = calculate_route(synthetic_graph, nodes, segments, "a", "d")

    assert route.node_ids == ["a", "c", "d"]
    assert route.segment_ids == ["seg_ac", "seg_cd"]
    assert route.total_distance_km == 100.0
    assert route.estimated_travel_time_min == 80.0


def test_synthetic_graph_has_alternative_path(synthetic_graph):
    assert has_alternative_path(synthetic_graph, "a", "d") is True


# ---------------------------------------------------------------------------
# Valid routes on the real corridor: node order, segment order, distance,
# travel time, geometry — between the 7 demonstration locations.
# ---------------------------------------------------------------------------

def test_valid_route_guwahati_to_tawang(network, graph):
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    assert route.origin == town_id(nodes, "Guwahati")
    assert route.destination == town_id(nodes, "Tawang")
    assert route.route_id.startswith("route_")
    assert route.created_at is not None
    assert route.total_distance_km > 0
    assert route.estimated_travel_time_min > 0


def test_route_distance_matches_sum_of_segments(network, graph):
    nodes, segments = network
    segments_by_id = {s.id: s for s in segments}
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    expected = sum(segments_by_id[sid].distance_km for sid in route.segment_ids)
    assert route.total_distance_km == pytest.approx(round(expected, 2))


def test_route_travel_time_matches_sum_of_segments(network, graph):
    nodes, segments = network
    segments_by_id = {s.id: s for s in segments}
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    expected = sum(segments_by_id[sid].estimated_travel_time_min for sid in route.segment_ids)
    assert route.estimated_travel_time_min == pytest.approx(round(expected, 1))


def test_route_geometry_is_continuous_real_road_geometry(network, graph):
    """Geometry must be assembled from real per-segment geometry (not
    straight lines between towns)."""
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    assert len(route.geometry) > 100  # real road polylines have many points

    # No jump anywhere near the scale of skipping/misordering a town (towns
    # are 40-180km apart). The real ordering/direction check is the
    # total-length comparison below: a reversed or misordered segment would
    # inflate polyline_km well past total_distance_km, which it does not.
    for p1, p2 in zip(route.geometry, route.geometry[1:]):
        step_km = haversine_km(p1.lat, p1.lng, p2.lat, p2.lng)
        assert step_km < 5.0, f"unexpected jump of {step_km:.2f} km in route geometry"

    nodes_by_id = {n.id: n for n in nodes}
    guwahati = nodes_by_id[town_id(nodes, "Guwahati")]
    tawang = nodes_by_id[town_id(nodes, "Tawang")]
    assert haversine_km(route.geometry[0].lat, route.geometry[0].lng, guwahati.lat, guwahati.lng) < 0.5
    assert haversine_km(route.geometry[-1].lat, route.geometry[-1].lng, tawang.lat, tawang.lng) < 0.5


def test_route_geometry_total_length_matches_distance(network, graph):
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    polyline_km = sum(
        haversine_km(a.lat, a.lng, b.lat, b.lng)
        for a, b in zip(route.geometry, route.geometry[1:])
    )
    assert polyline_km == pytest.approx(route.total_distance_km, rel=0.02)


@pytest.mark.parametrize("origin,destination", list(zip(CORRIDOR_ORDER, CORRIDOR_ORDER[1:])))
def test_routing_works_between_all_consecutive_demonstration_locations(network, graph, origin, destination):
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, origin, destination)
    assert route.origin == town_id(nodes, origin)
    assert route.destination == town_id(nodes, destination)
    assert route.total_distance_km > 0
    assert len(route.segment_ids) > 0


# ---------------------------------------------------------------------------
# Invalid origin/destination handling.
# ---------------------------------------------------------------------------

def test_invalid_origin_raises(network, graph):
    nodes, segments = network
    with pytest.raises(UnknownLocationError):
        calculate_route(graph, nodes, segments, "Atlantis", "Tawang")


def test_invalid_destination_raises(network, graph):
    nodes, segments = network
    with pytest.raises(UnknownLocationError):
        calculate_route(graph, nodes, segments, "Guwahati", "Atlantis")


# ---------------------------------------------------------------------------
# Disconnected nodes / no-path handling (synthetic — an isolated node has no
# real-world equivalent to safely test against the live dataset without
# mutating it).
# ---------------------------------------------------------------------------

def test_no_path_between_disconnected_nodes():
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    isolated = Node(id="isolated", name="Isolated", lat=50.0, lng=50.0, type=NodeType.town)
    nodes = [a, isolated]
    segments: list[RoadSegment] = []
    graph = build_graph(nodes, segments)

    with pytest.raises(NoRouteFoundError):
        calculate_route(graph, nodes, segments, "a", "isolated")


# ---------------------------------------------------------------------------
# Nearest-node lookup (by name and by coordinates) for the 7 demonstration
# locations.
# ---------------------------------------------------------------------------

def test_resolve_location_by_name(network):
    nodes, _ = network
    node = resolve_location(nodes, "Guwahati")
    assert node.name == "Guwahati"


def test_resolve_location_by_name_case_insensitive(network):
    nodes, _ = network
    node = resolve_location(nodes, "gUwAhAtI")
    assert node.name == "Guwahati"


def test_resolve_location_by_node_id(network):
    nodes, _ = network
    guwahati = resolve_location(nodes, "Guwahati")
    node = resolve_location(nodes, guwahati.id)
    assert node.id == guwahati.id


def test_resolve_location_by_coordinates_nearest_node(network):
    """In a dense real graph (thousands of nodes a few tens/hundreds of
    meters apart), the probe point needs to be genuinely close to the
    target node — a loose "somewhere near this town" coordinate can easily
    resolve to a different nearby junction instead. Probe a few meters from
    Tawang's actual matched node, not from the town's approximate center."""
    nodes, _ = network
    tawang = resolve_location(nodes, "Tawang")
    near_tawang = GeoPoint(lat=tawang.lat + 0.0001, lng=tawang.lng + 0.0001)  # ~15m away
    node = resolve_location(nodes, near_tawang)
    assert node.id == tawang.id


def test_resolve_location_unknown_string_raises(network):
    nodes, _ = network
    with pytest.raises(UnknownLocationError):
        resolve_location(nodes, "nowhere")


def test_calculate_route_accepts_coordinates_for_origin_and_destination(network, graph):
    nodes, segments = network
    guwahati = resolve_location(nodes, "Guwahati")
    tawang = resolve_location(nodes, "Tawang")
    near_guwahati = GeoPoint(lat=guwahati.lat + 0.0001, lng=guwahati.lng)
    near_tawang = GeoPoint(lat=tawang.lat + 0.0001, lng=tawang.lng)

    route = calculate_route(graph, nodes, segments, near_guwahati, near_tawang)
    assert route.origin == guwahati.id
    assert route.destination == tawang.id


# ---------------------------------------------------------------------------
# Alternative-route validation (real data, not fabricated):
#   Case A: a leg where an alternate path genuinely exists in the OSM data.
#   Case B: a leg where blocking the only real connection leaves no route.
# ---------------------------------------------------------------------------

def test_case_a_tezpur_to_bhalukpong_has_a_genuine_alternative(network, graph):
    nodes, _ = network
    origin = town_id(nodes, "Tezpur")
    destination = town_id(nodes, "Bhalukpong")
    assert has_alternative_path(graph, origin, destination) is True


def test_case_b_dirang_to_sela_pass_has_no_alternative(network, graph):
    """The high-mountain approach to Sela Pass has, in this real extract,
    exactly one road — there is no parallel/alternate route to remove and
    still connect these two points."""
    nodes, _ = network
    origin = town_id(nodes, "Dirang")
    destination = town_id(nodes, "Sela Pass")
    assert has_alternative_path(graph, origin, destination) is False


def test_case_b_blocking_the_only_route_leaves_no_feasible_path(network, graph):
    """More literally than has_alternative_path: physically remove the
    current route's edges (simulating that access being blocked) from a
    COPY of the graph, and confirm calculate_route reports no route at all
    — not silently falling back to some other path."""
    nodes, segments = network
    origin = town_id(nodes, "Dirang")
    destination = town_id(nodes, "Sela Pass")

    route = calculate_route(graph, nodes, segments, origin, destination)
    blocked = graph.copy()
    for u, v in zip(route.node_ids, route.node_ids[1:]):
        blocked.remove_edge(u, v)
        if blocked.has_edge(v, u):
            blocked.remove_edge(v, u)

    with pytest.raises(NoRouteFoundError):
        calculate_route(blocked, nodes, segments, origin, destination)


def test_case_a_blocking_one_path_still_leaves_a_route(network, graph):
    """Contrast with case B: blocking the Tezpur-Bhalukpong shortest path's
    edges should still leave a working (if different/longer) route, because
    a genuine alternative exists there."""
    nodes, segments = network
    origin = town_id(nodes, "Tezpur")
    destination = town_id(nodes, "Bhalukpong")

    route = calculate_route(graph, nodes, segments, origin, destination)
    blocked = graph.copy()
    for u, v in zip(route.node_ids, route.node_ids[1:]):
        blocked.remove_edge(u, v)
        if blocked.has_edge(v, u):
            blocked.remove_edge(v, u)

    alternative_route = calculate_route(blocked, nodes, segments, origin, destination)
    assert alternative_route.origin == origin
    assert alternative_route.destination == destination
    assert alternative_route.node_ids != route.node_ids


# ---------------------------------------------------------------------------
# oneway handling affects actual routing, not just graph structure.
# ---------------------------------------------------------------------------

def test_routing_never_traverses_a_oneway_segment_backwards(network, graph):
    """For every oneway=yes segment on the shortest Guwahati->Tawang route,
    confirm the route travels it in its allowed (from_node_id -> to_node_id)
    direction only."""
    nodes, segments = network
    segments_by_id = {s.id: s for s in segments}
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")

    for u, v, seg_id in zip(route.node_ids, route.node_ids[1:], route.segment_ids):
        segment = segments_by_id[seg_id]
        if not segment.bidirectional:
            assert (u, v) == (segment.from_node_id, segment.to_node_id), (
                f"{seg_id} is oneway but route traversed it {u} -> {v}, "
                f"opposite its allowed direction {segment.from_node_id} -> {segment.to_node_id}"
            )
