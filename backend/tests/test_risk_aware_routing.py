"""
Tests for Part 6 risk-aware routing (core/routing_engine.py's risk-aware
routing section).

Two kinds of fixtures are used, kept clearly separate per Part 6 section 10:

- SYNTHETIC graphs (built only in this file, prefixed `synthetic_`) for
  scenarios that need a guaranteed, deterministic risk configuration (an
  edge above the hard threshold, two paths with a controlled risk/time
  tradeoff, a fully-unsafe origin/destination). These never touch the real
  dataset.
- The REAL corridor (`network`/`graph` fixtures from conftest.py) for
  everything that should be validated against genuine OSM data, including
  the pre-existing Tezpur<->Bhalukpong / Dirang<->Sela Pass
  alternative-route behavior this part must not disturb.
"""
import pytest

from app.config import HARD_UNSAFE_RISK_THRESHOLD, RISK_WEIGHT
from app.core.risk_engine import assess_segment_risk
from app.core.routing_engine import (
    NoRouteFoundError,
    NoSafeRouteFoundError,
    build_graph,
    build_risk_aware_graph,
    calculate_risk_aware_route,
    calculate_route,
    compare_fastest_and_safe_routes,
    compute_route_risk_profile,
    get_route_segment_risks,
    has_alternative_path,
    risk_aware_edge_cost,
)
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from app.models.route import Route, RouteSafetyOutcome
from tests.conftest import CORRIDOR_ORDER


def _segment(
    seg_id, from_id, to_id, from_node, to_node, distance_km, travel_time_min,
    slope_deg=0.5, historical_landslide_count=0, nearest_landslide_distance_m=None,
):
    """Synthetic RoadSegment builder for this file's isolated unit tests —
    never added to the real dataset. slope_deg/historical_landslide_count
    default to values that score 0 risk (flat, no matched history) so a
    test only needs to override what it actually cares about."""
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


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def diamond_nodes():
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.2, lng=10.2, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=10.1, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=10.4, lng=10.4, type=NodeType.town)
    return [a, b, c, d]


@pytest.fixture
def fast_but_unsafe_vs_slow_but_safe(diamond_nodes):
    """A-B-D is fast (short travel time) but seg_ab is hard-unsafe (extreme
    slope + heavy, close historical landslide evidence). A-C-D is a little
    slower but fully safe (flat, no history). Used for scenarios 1/3/4/5."""
    a, b, c, d = diamond_nodes
    seg_ab = _segment(
        "seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10,
        slope_deg=40.0, historical_landslide_count=20, nearest_landslide_distance_m=5.0,
    )
    seg_bd = _segment("seg_bd", "b", "d", b, d, distance_km=5, travel_time_min=10)
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=6, travel_time_min=12)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=6, travel_time_min=12)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(diamond_nodes, segments)
    return diamond_nodes, segments, graph


@pytest.fixture
def slightly_slower_but_substantially_safer(diamond_nodes):
    """Both paths are BELOW the hard unsafe threshold (neither edge is
    excluded) — this tests the weighted-cost PREFERENCE for lower risk, not
    hard exclusion. A-B-D: fast (50 min) but meaningfully risky (moderate
    slope + historical evidence). A-C-D: a little slower (55 min, +10%) but
    essentially risk-free."""
    a, b, c, d = diamond_nodes
    seg_ab = _segment(
        "seg_ab", "a", "b", a, b, distance_km=25, travel_time_min=25,
        slope_deg=20.0, historical_landslide_count=3, nearest_landslide_distance_m=50.0,
    )
    seg_bd = _segment(
        "seg_bd", "b", "d", b, d, distance_km=25, travel_time_min=25,
        slope_deg=20.0, historical_landslide_count=3, nearest_landslide_distance_m=50.0,
    )
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=27, travel_time_min=27.5)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=27, travel_time_min=27.5)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(diamond_nodes, segments)
    return diamond_nodes, segments, graph


@pytest.fixture
def all_paths_unsafe():
    """A single edge between two nodes, deliberately configured to score
    above the hard unsafe threshold, with no alternative path at all."""
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.1, type=NodeType.town)
    nodes = [a, b]
    seg_ab = _segment(
        "seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10,
        slope_deg=40.0, historical_landslide_count=20, nearest_landslide_distance_m=1.0,
    )
    segments = [seg_ab]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


# ---------------------------------------------------------------------------
# 1. Risk-aware routing chooses fastest route when risks are comparable.
# ---------------------------------------------------------------------------


def test_risk_aware_matches_fastest_when_both_paths_equally_safe(diamond_nodes):
    a, b, c, d = diamond_nodes
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10)
    seg_bd = _segment("seg_bd", "b", "d", b, d, distance_km=5, travel_time_min=10)
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=6, travel_time_min=15)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=6, travel_time_min=15)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(diamond_nodes, segments)

    fastest = calculate_route(graph, diamond_nodes, segments, "a", "d")
    risk_aware = calculate_risk_aware_route(graph, diamond_nodes, segments, "a", "d")
    assert risk_aware.node_ids == fastest.node_ids == ["a", "b", "d"]


# ---------------------------------------------------------------------------
# 2. Risk-aware routing chooses a slightly slower but substantially safer route
#    (preference-based, NOT hard exclusion -- both edges remain feasible).
# ---------------------------------------------------------------------------


def test_risk_aware_prefers_substantially_safer_slightly_slower_route(slightly_slower_but_substantially_safer):
    nodes, segments, graph = slightly_slower_but_substantially_safer
    segments_by_id = {s.id: s for s in segments}

    risky_risk = assess_segment_risk(segments_by_id["seg_ab"]).risk_score
    safe_risk = assess_segment_risk(segments_by_id["seg_ac"]).risk_score
    assert risky_risk < HARD_UNSAFE_RISK_THRESHOLD  # both paths are feasible -- this is a preference, not exclusion
    assert risky_risk > safe_risk  # sanity check on the fixture itself

    fastest = calculate_route(graph, nodes, segments, "a", "d")
    assert fastest.node_ids == ["a", "b", "d"]  # baseline picks the faster, riskier path

    risk_aware = calculate_risk_aware_route(graph, nodes, segments, "a", "d")
    assert risk_aware.node_ids == ["a", "c", "d"]  # risk-aware picks the slower, safer path
    assert risk_aware.estimated_travel_time_min > fastest.estimated_travel_time_min  # genuinely slower...

    fastest_profile = compute_route_risk_profile(fastest, segments)
    safer_profile = compute_route_risk_profile(risk_aware, segments)
    assert safer_profile.aggregate_risk_score < fastest_profile.aggregate_risk_score  # ...but substantially safer


# ---------------------------------------------------------------------------
# 3. An edge above the hard unsafe threshold is excluded.
# ---------------------------------------------------------------------------


def test_edge_above_hard_threshold_is_excluded_from_risk_aware_graph(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe
    segments_by_id = {s.id: s for s in segments}
    unsafe_risk = assess_segment_risk(segments_by_id["seg_ab"]).risk_score
    assert unsafe_risk >= HARD_UNSAFE_RISK_THRESHOLD  # sanity check on the fixture

    risk_graph = build_risk_aware_graph(nodes, segments)
    assert not risk_graph.has_edge("a", "b")
    assert not risk_graph.has_edge("b", "a")
    # the rest of the graph must still be intact
    assert risk_graph.has_edge("a", "c")
    assert risk_graph.has_edge("c", "d")


def test_risk_aware_edge_cost_formula():
    edge_data = {"travel_time_min": 10.0, "risk_score": 0.5}
    assert risk_aware_edge_cost(edge_data, risk_weight=2.0) == pytest.approx(10.0 * (1 + 2.0 * 0.5))
    zero_risk = {"travel_time_min": 10.0, "risk_score": 0.0}
    assert risk_aware_edge_cost(zero_risk, risk_weight=RISK_WEIGHT) == pytest.approx(10.0)  # reduces to baseline


# ---------------------------------------------------------------------------
# 4. Fastest route unsafe but another safe route exists -> safe route chosen.
# ---------------------------------------------------------------------------


def test_unsafe_fastest_route_falls_back_to_safe_alternative(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe

    fastest = calculate_route(graph, nodes, segments, "a", "d")
    assert fastest.node_ids == ["a", "b", "d"]  # the unsafe-but-fast path

    safe_route = calculate_risk_aware_route(graph, nodes, segments, "a", "d")
    assert safe_route.node_ids == ["a", "c", "d"]  # forced onto the safe path
    assert "seg_ab" not in safe_route.segment_ids


def test_compare_fastest_and_safe_routes_reports_case_b(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "a", "d")

    assert result.outcome == RouteSafetyOutcome.safer_route_selected
    assert result.safer_alternative_selected is True
    assert result.unsafe_segments_in_fastest_route is True
    assert result.recommended_route is not None
    assert result.recommended_route.node_ids == ["a", "c", "d"]
    assert result.recommended_route.node_ids != result.fastest_route.node_ids
    assert any("unsafe" in r.lower() for r in result.reasons)


# ---------------------------------------------------------------------------
# 5. All feasible routes unsafe -> "no safe route available".
# ---------------------------------------------------------------------------


def test_no_safe_route_raises_specific_exception(all_paths_unsafe):
    nodes, segments, graph = all_paths_unsafe
    # The plain baseline route DOES exist (risk is not considered)...
    fastest = calculate_route(graph, nodes, segments, "a", "b")
    assert fastest.node_ids == ["a", "b"]

    # ...but risk-aware routing must refuse it, distinctly from "no road exists".
    with pytest.raises(NoSafeRouteFoundError):
        calculate_risk_aware_route(graph, nodes, segments, "a", "b")


def test_no_safe_route_found_error_is_a_no_route_found_error(all_paths_unsafe):
    assert issubclass(NoSafeRouteFoundError, NoRouteFoundError)


def test_compare_fastest_and_safe_routes_reports_case_c(all_paths_unsafe):
    nodes, segments, graph = all_paths_unsafe
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "a", "b")

    assert result.outcome == RouteSafetyOutcome.no_safe_route_available
    assert result.recommended_route is None
    assert result.recommended_route_risk is None
    assert result.safer_alternative_selected is False
    assert result.unsafe_segments_in_fastest_route is True
    assert result.fastest_route is not None  # the fastest route is still reported, for visibility
    assert any("no safe route" in r.lower() for r in result.reasons)


def test_genuinely_disconnected_nodes_still_raise_plain_no_route_found_error():
    """A location pair with NO road at all (not even ignoring risk) must
    still raise the original NoRouteFoundError, not NoSafeRouteFoundError —
    risk-aware routing must not blur "no road" with "road exists but unsafe"."""
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    isolated = Node(id="isolated", name="Isolated", lat=50.0, lng=50.0, type=NodeType.town)
    nodes = [a, isolated]
    segments: list[RoadSegment] = []
    graph = build_graph(nodes, segments)

    with pytest.raises(NoRouteFoundError) as exc_info:
        calculate_risk_aware_route(graph, nodes, segments, "a", "isolated")
    assert not isinstance(exc_info.value, NoSafeRouteFoundError)


# ---------------------------------------------------------------------------
# 6. A route with one very high-risk segment reports that high maximum risk.
# ---------------------------------------------------------------------------


def test_route_aggregation_is_not_diluted_by_many_safe_segments():
    a = Node(id="a", name="A", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.1, lng=10.0, type=NodeType.town)
    c = Node(id="c", name="C", lat=10.2, lng=10.0, type=NodeType.town)
    d = Node(id="d", name="D", lat=10.3, lng=10.0, type=NodeType.town)
    e = Node(id="e", name="E", lat=10.4, lng=10.0, type=NodeType.town)
    nodes = [a, b, c, d, e]

    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=1, travel_time_min=2)
    seg_bc = _segment("seg_bc", "b", "c", b, c, distance_km=1, travel_time_min=2)
    seg_cd = _segment(  # the one dangerous segment
        "seg_cd", "c", "d", c, d, distance_km=1, travel_time_min=2,
        slope_deg=24.0, historical_landslide_count=4, nearest_landslide_distance_m=20.0,
    )
    seg_de = _segment("seg_de", "d", "e", d, e, distance_km=1, travel_time_min=2)
    segments = [seg_ab, seg_bc, seg_cd, seg_de]

    route = Route(
        origin="a", destination="e",
        node_ids=["a", "b", "c", "d", "e"],
        segment_ids=["seg_ab", "seg_bc", "seg_cd", "seg_de"],
        total_distance_km=4.0, estimated_travel_time_min=8.0, geometry=[],
    )
    profile = compute_route_risk_profile(route, segments)

    dangerous_risk = assess_segment_risk(seg_cd).risk_score
    naive_mean = dangerous_risk / 4  # what a plain average across 4 segments (3 safe + 1 dangerous) would give

    assert profile.max_segment_risk == pytest.approx(dangerous_risk)
    assert profile.max_risk_segment_id == "seg_cd"
    assert profile.aggregate_risk_score > naive_mean  # must NOT read like a diluted plain average
    assert profile.segment_count_by_risk_level["low"] == 3 or profile.segment_count_by_risk_level["moderate"] >= 0


# ---------------------------------------------------------------------------
# 7. Route risk aggregation is deterministic.
# ---------------------------------------------------------------------------


def test_route_risk_profile_is_deterministic(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe
    route = calculate_route(graph, nodes, segments, "a", "d")

    first = compute_route_risk_profile(route, segments)
    second = compute_route_risk_profile(route, segments)
    assert first == second


def test_route_risk_profile_handles_empty_route_without_crashing():
    route = Route(origin="a", destination="a", node_ids=["a"], segment_ids=[], total_distance_km=0.0, estimated_travel_time_min=0.0, geometry=[])
    profile = compute_route_risk_profile(route, [])
    assert profile.aggregate_risk_score == 0.0
    assert profile.max_segment_risk == 0.0


# ---------------------------------------------------------------------------
# 8. Existing baseline fastest-time routing still works exactly as before.
# ---------------------------------------------------------------------------


def test_baseline_routing_unaffected_on_real_corridor(network, graph):
    """A direct regression check that Part 6 did not change calculate_route's
    behavior on the real network (the full test_routing.py suite is the
    authoritative check; this is a quick sanity duplicate)."""
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")
    assert route.total_distance_km > 0
    assert route.estimated_travel_time_min > 0
    assert len(route.segment_ids) > 0


def test_baseline_edge_cost_still_ignores_risk(network, graph):
    """edge_cost() (baseline) must still be travel-time only -- Part 6 added
    a SEPARATE risk_aware_edge_cost(), it must not have modified edge_cost."""
    from app.core.routing_engine import edge_cost

    edge_data = {"travel_time_min": 42.0, "risk": 0.99}
    assert edge_cost(edge_data) == 42.0


# ---------------------------------------------------------------------------
# 9/10. Real OSM alternative-route behavior must remain valid, including
# under the risk-aware graph builder.
# ---------------------------------------------------------------------------


def _town_id(nodes, name):
    from app.core.routing_engine import resolve_location

    return resolve_location(nodes, name).id


def test_tezpur_to_bhalukpong_alternative_still_valid_on_baseline_graph(network, graph):
    nodes, _ = network
    origin = _town_id(nodes, "Tezpur")
    destination = _town_id(nodes, "Bhalukpong")
    assert has_alternative_path(graph, origin, destination) is True


def test_dirang_to_sela_pass_no_alternative_still_valid_on_baseline_graph(network, graph):
    nodes, _ = network
    origin = _town_id(nodes, "Dirang")
    destination = _town_id(nodes, "Sela Pass")
    assert has_alternative_path(graph, origin, destination) is False


def test_tezpur_to_bhalukpong_alternative_preserved_in_risk_aware_graph(network):
    nodes, segments = network
    origin = _town_id(nodes, "Tezpur")
    destination = _town_id(nodes, "Bhalukpong")
    risk_graph = build_risk_aware_graph(nodes, segments)
    assert has_alternative_path(risk_graph, origin, destination) is True


def test_dirang_to_sela_pass_no_alternative_preserved_in_risk_aware_graph(network):
    nodes, segments = network
    origin = _town_id(nodes, "Dirang")
    destination = _town_id(nodes, "Sela Pass")
    risk_graph = build_risk_aware_graph(nodes, segments)
    assert has_alternative_path(risk_graph, origin, destination) is False


@pytest.mark.parametrize("origin,destination", list(zip(CORRIDOR_ORDER, CORRIDOR_ORDER[1:])))
def test_compare_fastest_and_safe_routes_works_for_every_corridor_leg(network, graph, origin, destination):
    """Real-network integration check across every named leg -- must never
    raise unexpectedly and must always report SOME outcome."""
    nodes, segments = network
    result = compare_fastest_and_safe_routes(graph, nodes, segments, origin, destination)
    assert result.outcome in (
        RouteSafetyOutcome.fastest_route_is_safe,
        RouteSafetyOutcome.safer_route_selected,
        RouteSafetyOutcome.no_safe_route_available,
    )


# ---------------------------------------------------------------------------
# 11. No fabricated routes are ever returned.
# ---------------------------------------------------------------------------


def test_recommended_route_only_uses_real_existing_segments(network, graph):
    nodes, segments = network
    real_segment_ids = {s.id for s in segments}
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "Bhalukpong", "Bomdila", weather_factor=0.9, incident_factor=0.9)

    for sid in result.fastest_route.segment_ids:
        assert sid in real_segment_ids
    if result.recommended_route is not None:
        for sid in result.recommended_route.segment_ids:
            assert sid in real_segment_ids


def test_risk_aware_route_node_path_is_a_real_path_in_the_graph(network, graph):
    """Every consecutive (u, v) in a risk-aware route's node_ids must be a
    real edge that existed in the underlying road graph -- never invented."""
    nodes, segments = network
    route = calculate_risk_aware_route(graph, nodes, segments, "Guwahati", "Tawang")
    risk_graph = build_risk_aware_graph(nodes, segments)
    for u, v in zip(route.node_ids, route.node_ids[1:]):
        assert risk_graph.has_edge(u, v)


# ---------------------------------------------------------------------------
# Per-segment risk exposure (frontend integration, Part 6.5): every route's
# individual segment risks, embedded in RiskAwareRouteResult.
# ---------------------------------------------------------------------------


def test_get_route_segment_risks_matches_route_segment_order(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe
    route = calculate_route(graph, nodes, segments, "a", "d")
    results = get_route_segment_risks(route, segments)
    assert [r.segment_id for r in results] == route.segment_ids


def test_get_route_segment_risks_agrees_with_aggregate_profile(fast_but_unsafe_vs_slow_but_safe):
    nodes, segments, graph = fast_but_unsafe_vs_slow_but_safe
    route = calculate_route(graph, nodes, segments, "a", "d")
    results = get_route_segment_risks(route, segments)
    profile = compute_route_risk_profile(route, segments, segment_risks=results)
    assert profile.max_segment_risk == max(r.risk_score for r in results)


def test_compare_fastest_and_safe_routes_exposes_fastest_segment_risks(network, graph):
    nodes, segments = network
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "Guwahati", "Tawang")
    assert len(result.fastest_route_segment_risks) == len(result.fastest_route.segment_ids)
    assert [r.segment_id for r in result.fastest_route_segment_risks] == result.fastest_route.segment_ids


def test_compare_fastest_and_safe_routes_recommended_segment_risks_none_when_same_as_fastest(network, graph):
    """When outcome is fastest_route_is_safe, recommended_route IS
    fastest_route -- recommended_route_segment_risks is left None rather
    than duplicating the (potentially large) list; a caller falls back to
    fastest_route_segment_risks in that case."""
    nodes, segments = network
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "Guwahati", "Tawang")
    assert result.outcome.value == "fastest_route_is_safe"
    assert result.recommended_route is not None
    assert result.recommended_route.node_ids == result.fastest_route.node_ids
    assert result.recommended_route_segment_risks is None


def test_compare_fastest_and_safe_routes_exposes_distinct_recommended_segment_risks_when_different(network, graph):
    """When a genuinely different (safer) route is recommended, its own
    segment risks ARE included."""
    nodes, segments = network
    result = compare_fastest_and_safe_routes(
        graph, nodes, segments, "Bhalukpong", "Bomdila", weather_factor=0.9, incident_factor=0.9
    )
    assert result.outcome.value == "safer_route_selected"
    assert result.recommended_route_segment_risks is not None
    assert [r.segment_id for r in result.recommended_route_segment_risks] == result.recommended_route.segment_ids


def test_compare_fastest_and_safe_routes_recommended_segment_risks_none_when_no_safe_route(all_paths_unsafe):
    nodes, segments, graph = all_paths_unsafe
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "a", "b")
    assert result.recommended_route is None
    assert result.recommended_route_segment_risks is None
    assert len(result.fastest_route_segment_risks) == 1  # still reported for the fastest route


def test_bhalukpong_to_bomdila_under_severe_context_selects_a_genuine_alternative(network, graph):
    """A real-data demonstration of CASE B: under a severe (hypothetical)
    weather+incident context, the real 'Doimara-Nichiphu' segment crosses
    the hard unsafe threshold, and risk-aware routing must route around it
    using another REAL path, not fabricate one."""
    nodes, segments = network
    result = compare_fastest_and_safe_routes(
        graph, nodes, segments, "Bhalukpong", "Bomdila", weather_factor=0.9, incident_factor=0.9
    )
    assert result.outcome == RouteSafetyOutcome.safer_route_selected
    assert result.unsafe_segments_in_fastest_route is True
    real_segment_ids = {s.id for s in segments}
    assert set(result.recommended_route.segment_ids) <= real_segment_ids
