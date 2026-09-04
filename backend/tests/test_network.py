"""
Tests for the real OSM GeoJSON road network integration: loading, OSM
attribute preservation, oneway handling, connectivity, distances, and
geometry preservation.

network/graph fixtures come from conftest.py. This corridor's node/edge
counts come from real OpenStreetMap data (not a fixed constant we chose),
so these tests check structural properties rather than exact counts.
"""
import networkx as nx
import pytest

from app.core.geo import haversine_km
from app.core.routing_engine import shortest_path_by_distance
from tests.conftest import CORRIDOR_ORDER


# 1. The GeoJSON loads successfully.

def test_network_loads(network):
    nodes, segments = network
    assert len(nodes) > 500, "expected a real, sizeable branched network, not a handful of nodes"
    assert len(segments) > 500


def test_expected_corridor_towns_present(network):
    """Each of the 7 demonstration locations was matched (nearest-node
    resolution) to a real node and tagged with its name."""
    nodes, _ = network
    named = {n.name for n in nodes if n.name}
    assert set(CORRIDOR_ORDER) <= named


# 2. OSM road attributes are preserved.

def test_osm_attributes_preserved(network):
    _, segments = network
    with_name = [s for s in segments if s.name]
    with_ref = [s for s in segments if s.ref]
    with_osm_id = [s for s in segments if s.osm_way_ids]
    assert len(with_name) > 100
    assert len(with_ref) > 100
    assert len(with_osm_id) == len(segments), "every segment should trace back to a source OSM way"

    # a specific, known real feature: the Saraighat Bridge, NH27, oneway
    bridge = [s for s in segments if s.name == "Saraighat Bridge"]
    assert len(bridge) == 1
    assert bridge[0].ref == "NH27"
    assert bridge[0].road_type == "trunk"
    assert bridge[0].oneway == "yes"
    assert bridge[0].osm_way_ids == [38719482]


def test_road_type_values_are_real_osm_highway_classes(network):
    _, segments = network
    seen = {s.road_type for s in segments}
    # every value actually present in the source GeoJSON's `highway` tag
    assert seen <= {
        "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    }
    assert "trunk" in seen and "primary" in seen  # sanity: this corridor has both


# 3. oneway handling.

def test_oneway_yes_segments_exist_and_are_marked_not_bidirectional(network):
    _, segments = network
    oneway_segments = [s for s in segments if s.oneway == "yes"]
    assert len(oneway_segments) > 0
    assert all(not s.bidirectional for s in oneway_segments)


def test_segments_without_oneway_yes_stay_bidirectional(network):
    _, segments = network
    for s in segments:
        if s.oneway != "yes":
            assert s.bidirectional, f"{s.id} has oneway={s.oneway!r} but was marked one-way"


def test_oneway_segment_produces_single_directed_edge_in_graph(network, graph):
    """The graph itself, not just the RoadSegment flag, must respect oneway:
    exactly one direction traversable for a oneway=yes segment."""
    _, segments = network
    bridge = next(s for s in segments if s.name == "Saraighat Bridge")
    assert graph.has_edge(bridge.from_node_id, bridge.to_node_id)
    assert not graph.has_edge(bridge.to_node_id, bridge.from_node_id)


def test_bidirectional_segment_produces_both_directed_edges_in_graph(network, graph):
    _, segments = network
    two_way = next(s for s in segments if s.bidirectional)
    assert graph.has_edge(two_way.from_node_id, two_way.to_node_id)
    assert graph.has_edge(two_way.to_node_id, two_way.from_node_id)


# 4. Nodes/segments are connected correctly; genuine branching.

def test_segments_reference_known_nodes(network):
    nodes, segments = network
    node_ids = {n.id for n in nodes}
    for segment in segments:
        assert segment.from_node_id in node_ids
        assert segment.to_node_id in node_ids


def test_demonstration_towns_share_one_connected_component(network, graph):
    """The 7 demo towns must all be mutually reachable (ignoring one-way
    direction) even though the raw extract has some small disconnected
    fragments at its boundary (see backend/app/data/README.md limitations)."""
    nodes, _ = network
    named_ids = {n.id for n in nodes if n.name}
    components = list(nx.weakly_connected_components(graph))
    main_component = max(components, key=len)
    assert named_ids <= main_component


def test_network_has_genuine_branching(graph):
    """A meaningful sanity check that this is a real branched network, not a
    disguised single chain: many nodes must have more than 2 connections."""
    branch_points = [n for n in graph.nodes if graph.degree(n) > 2]
    assert len(branch_points) > 50


# 5. Distances are represented correctly.

def test_distances_are_positive_and_plausible(network):
    _, segments = network
    for segment in segments:
        assert segment.distance_km > 0
        assert segment.distance_km < 100  # individual intersection-to-intersection edges


def test_distance_roughly_matches_geometry_length(network):
    _, segments = network
    for segment in segments:
        pts = segment.geometry
        polyline_km = sum(
            haversine_km(pts[i].lat, pts[i].lng, pts[i + 1].lat, pts[i + 1].lng)
            for i in range(len(pts) - 1)
        )
        # rel=0.01 alone is too strict for the shortest segments (a few
        # meters), where a 4-decimal-place km rounding is already a few cm
        # by itself — abs=0.001 (1m) covers that without weakening the
        # check for any normal-length segment.
        assert polyline_km == pytest.approx(segment.distance_km, rel=0.01, abs=0.001)


# 6. The graph can identify a path between selected origin and destination nodes.

def test_graph_has_all_nodes(graph, network):
    nodes, _ = network
    assert graph.number_of_nodes() == len(nodes)


def test_shortest_path_guwahati_to_tawang(graph, network):
    """Note: the pure-distance shortest path between the two endpoints is
    NOT guaranteed to pass through every intermediate demonstration town's
    exact node — a real road network can have a real bypass shorter/faster
    than routing through a town center. That's expected and correct (see
    test_routing.py's parametrized per-leg test for coverage of routing
    between each pair of demonstration locations specifically)."""
    nodes, _ = network
    from app.core.routing_engine import resolve_location

    origin = resolve_location(nodes, "Guwahati").id
    destination = resolve_location(nodes, "Tawang").id
    path = shortest_path_by_distance(graph, origin, destination)
    assert path[0] == origin
    assert path[-1] == destination
    assert len(path) > 10


def test_no_path_to_unknown_node_raises(graph):
    with pytest.raises(nx.NodeNotFound):
        shortest_path_by_distance(graph, "n00000", "does_not_exist")


# 7. Geographic geometry is preserved for map rendering.

def test_every_segment_has_renderable_geometry(network):
    _, segments = network
    for segment in segments:
        assert len(segment.geometry) >= 2
        for point in segment.geometry:
            assert -90 <= point.lat <= 90
            assert -180 <= point.lng <= 180


def test_geometry_endpoints_match_connected_nodes_exactly(network):
    """Unlike the earlier OSRM-derived dataset, geometry here comes from the
    exact same coordinates used to build the graph nodes, so endpoints
    should match exactly (within floating-point rounding), not just
    approximately."""
    nodes, segments = network
    nodes_by_id = {n.id: n for n in nodes}
    for segment in segments:
        from_node = nodes_by_id[segment.from_node_id]
        to_node = nodes_by_id[segment.to_node_id]
        start, end = segment.geometry[0], segment.geometry[-1]

        assert haversine_km(start.lat, start.lng, from_node.lat, from_node.lng) < 0.001
        assert haversine_km(end.lat, end.lng, to_node.lat, to_node.lng) < 0.001
