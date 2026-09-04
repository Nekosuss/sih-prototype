"""
Regression tests for a reported (but not reproducible, see module-level
findings recorded in the final response to this fix) Part 8 rerouting issue
plus a genuine spot-check of the Part 10 `/weather/corridor` 404 report.

These tests exercise the REAL production code path -- the same functions/
endpoints the API layer calls -- against the REAL corridor network and the
REAL known Bhalukpong -> Bomdila / Doimara-Nichiphu scenario already
validated in Part 8 (see tests/test_hazard_response.py and
tests/test_api.py). They exist to LOCK IN the currently-correct behavior so
any future change that breaks it fails CI immediately, and to give a
directly-reproducible record of the investigation for this bug report.

Investigation summary (see final response for full detail): direct
reproduction through the actual backend/API confirms the reroute mechanism
is currently working correctly end-to-end for the exact reported scenario,
and `/weather/corridor` returns 200 (not 404) for 2023-01-11 and every
other date tested, including dates outside the extracted dataset's range
(which correctly return a `no_coverage` status with HTTP 200, never a 404).
Neither hazard_state.py, routing_engine.py, nor reroute_service.py was
modified by Parts 10/11 (grep-verified: no "Part 10"/"Part 11" markers in
any of the three files), which is consistent with this finding.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.hazard_state import build_hazard_event, combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.risk_engine import assess_segment_risk
from app.core.routing_engine import calculate_route
from app.models.hazard import HazardSeverity, HazardType
from app.models.route import RouteDecisionOutcome


@pytest.fixture
def doimara_nichiphu_segment_ids(network):
    _, segments = network
    ids = [s.id for s in segments if s.name == "Doimara-Nichiphu"]
    assert ids, "expected the real network to contain 'Doimara-Nichiphu' segments"
    return ids


# ---------------------------------------------------------------------------
# 1-2. Normal route works; known blocking segment(s) are actually on it.
# ---------------------------------------------------------------------------


def test_1_bhalukpong_bomdila_normal_route_works(network, graph):
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Bhalukpong", "Bomdila")
    assert route.total_distance_km == pytest.approx(98.22, abs=0.5)
    assert len(route.segment_ids) > 0


def test_2_doimara_nichiphu_segments_are_on_the_normal_route(network, graph, doimara_nichiphu_segment_ids):
    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Bhalukpong", "Bomdila")
    on_route = [sid for sid in doimara_nichiphu_segment_ids if sid in route.segment_ids]
    assert on_route, "expected at least one real Doimara-Nichiphu segment on the baseline route"


# ---------------------------------------------------------------------------
# 3. Blocking removes the segment from the risk-aware routing graph.
# ---------------------------------------------------------------------------


def test_3_blocking_hazard_excludes_segment_from_risk_aware_graph(network, graph, doimara_nichiphu_segment_ids):
    from app.core.routing_engine import build_risk_aware_graph

    nodes, segments = network
    route = calculate_route(graph, nodes, segments, "Bhalukpong", "Bomdila")
    blocked = [sid for sid in doimara_nichiphu_segment_ids if sid in route.segment_ids]

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, blocked)
    context = combine_active_hazards_into_segment_context([event])
    assert all(context[sid].closed for sid in blocked), "blocking road_blockage must mark every affected segment closed"

    risk_graph = build_risk_aware_graph(nodes, segments, segment_context=context)
    segment_ids_in_graph = {data["segment_id"] for _, _, data in risk_graph.edges(data=True)}
    for sid in blocked:
        assert sid not in segment_ids_in_graph, f"{sid} must be excluded from the risk-aware graph once blocked"


# ---------------------------------------------------------------------------
# 4-6. Blocking causes a genuine, different alternative route with zero
# blocked segments -- the exact scenario reported as broken.
# ---------------------------------------------------------------------------


def test_4_5_6_blocking_doimara_nichiphu_forces_a_genuine_reroute(network, graph, doimara_nichiphu_segment_ids):
    nodes, segments = network

    baseline_decision = evaluate_route_decision(graph, nodes, segments, "Bhalukpong", "Bomdila")
    assert baseline_decision.outcome == RouteDecisionOutcome.continue_
    baseline_route = baseline_decision.recommended_route
    blocked = [sid for sid in doimara_nichiphu_segment_ids if sid in baseline_route.segment_ids]
    assert blocked

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, blocked)
    context = combine_active_hazards_into_segment_context([event])

    decision = evaluate_route_decision(
        graph, nodes, segments, "Bhalukpong", "Bomdila",
        previous_route=baseline_route, segment_context=context, active_hazard_ids=[event.id],
    )

    # 4/5: a genuine reroute to a DIFFERENT real route.
    assert decision.outcome == RouteDecisionOutcome.reroute
    assert decision.recommended_route is not None
    assert decision.recommended_route.node_ids != baseline_route.node_ids
    assert decision.recommended_route.route_id != baseline_route.route_id

    # 6: zero blocked segments on the new route.
    assert set(decision.recommended_route.segment_ids).isdisjoint(blocked)

    # The alternative must be real (a subset of the actual network's segment
    # ids -- never fabricated) and genuinely different in cost, consistent
    # with the known Part 6/8 finding for this exact corridor leg.
    real_segment_ids = {s.id for s in segments}
    assert set(decision.recommended_route.segment_ids) <= real_segment_ids
    assert decision.recommended_route.estimated_travel_time_min != baseline_route.estimated_travel_time_min


# ---------------------------------------------------------------------------
# 7. Clearing the hazard restores the original route.
# ---------------------------------------------------------------------------


def test_7_clearing_hazard_restores_the_original_route(network, graph, doimara_nichiphu_segment_ids):
    nodes, segments = network
    baseline_decision = evaluate_route_decision(graph, nodes, segments, "Bhalukpong", "Bomdila")
    baseline_route = baseline_decision.recommended_route
    blocked = [sid for sid in doimara_nichiphu_segment_ids if sid in baseline_route.segment_ids]

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, blocked)
    active_context = combine_active_hazards_into_segment_context([event])
    rerouted = evaluate_route_decision(
        graph, nodes, segments, "Bhalukpong", "Bomdila",
        previous_route=baseline_route, segment_context=active_context,
    )
    assert rerouted.outcome == RouteDecisionOutcome.reroute

    # "Clearing" the hazard means it no longer contributes to the combined
    # context (StateStore.clear_hazard marks it inactive; here we simply
    # don't include it, matching combine_active_hazards_into_segment_context's
    # documented "only ACTIVE hazards contribute" contract). Evaluated FRESH
    # (no previous_route/hysteresis in play -- that's a separate, orthogonal
    # concern already covered by test_hysteresis_* in test_hazard_response.py)
    # to isolate exactly what this test is about: once the hazard is gone,
    # the ORIGINAL real route must be routable and recommended again, not
    # still excluded as if it were closed.
    cleared_context = combine_active_hazards_into_segment_context([])
    restored = evaluate_route_decision(
        graph, nodes, segments, "Bhalukpong", "Bomdila", segment_context=cleared_context,
    )
    assert restored.outcome == RouteDecisionOutcome.continue_
    assert restored.recommended_route.node_ids == baseline_route.node_ids
    assert set(blocked) <= set(restored.recommended_route.segment_ids)  # the previously-blocked segments are usable again


# ---------------------------------------------------------------------------
# 8. No-safe-route still produces SUSPEND (synthetic, per Part 8's own
# documented convention -- the real corridor never naturally hits this).
# ---------------------------------------------------------------------------


def test_8_no_safe_route_still_suspends():
    from app.core.routing_engine import build_graph
    from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType

    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.1, type=NodeType.town)
    segment = RoadSegment(
        id="seg_ab", from_node_id="a", to_node_id="b", road_type=RoadType.tertiary,
        distance_km=5.0, estimated_travel_time_min=10.0,
        geometry=[GeoPoint(lat=a.lat, lng=a.lng), GeoPoint(lat=b.lat, lng=b.lng)],
        terrain_type=TerrainType.plain, slope_deg=0.5, elevation_m=500.0,
        landslide_susceptibility=0.0, flood_susceptibility=0.0, base_risk=0.05, current_risk_score=0.05,
    )
    nodes, segments = [a, b], [segment]
    graph = build_graph(nodes, segments)

    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])

    decision = evaluate_route_decision(graph, nodes, segments, "a", "b", segment_context=context)
    assert decision.outcome == RouteDecisionOutcome.suspend
    assert decision.recommended_route is None


# ---------------------------------------------------------------------------
# 9. APSAC hazard-layer no_coverage must never erase a dynamic
# blockage/incident -- these are independent inputs to the same segment.
# ---------------------------------------------------------------------------


def test_9_hazard_layer_no_coverage_does_not_erase_active_blockage(network, doimara_nichiphu_segment_ids):
    from app.data.hazard_layer_loader import HazardLayerStatus, get_default_hazard_layer_loader

    _, segments = network
    blocked_id = doimara_nichiphu_segment_ids[0]
    blocked_segment = next(s for s in segments if s.id == blocked_id)

    # Confirm the real, current state: no official APSAC layer is loaded,
    # so this segment's landslide/flood hazard-zonation query is no_coverage.
    loader = get_default_hazard_layer_loader()
    assert loader.get_landslide_hazard(
        blocked_segment.geometry[len(blocked_segment.geometry) // 2].lat,
        blocked_segment.geometry[len(blocked_segment.geometry) // 2].lng,
    ).status == HazardLayerStatus.no_coverage

    # The dynamic blockage must be completely independent of that -- closed
    # must still be True regardless of hazard-layer coverage.
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, [blocked_id])
    context = combine_active_hazards_into_segment_context([event])
    assert context[blocked_id].closed is True
    assert context[blocked_id].incident_factor == 1.0

    # And assess_segment_risk must not silently treat "no hazard-layer
    # coverage" as "safe" -- the incident contribution must still show up.
    result = assess_segment_risk(blocked_segment, incident_factor=context[blocked_id].incident_factor)
    assert result.breakdown.incident_risk == 1.0


# ---------------------------------------------------------------------------
# 10. Real rainfall integration must not erase dynamic hazard/incident context.
# ---------------------------------------------------------------------------


def test_10_rainfall_context_does_not_erase_active_blockage(network, doimara_nichiphu_segment_ids):
    from app.core.weather_factor import rainfall_segment_context

    _, segments = network
    blocked_id = doimara_nichiphu_segment_ids[0]

    # Real rainfall context for the default demo date, built completely
    # independently of any hazard -- confirms the two sources don't collide
    # when used together (the live API endpoints never merge them today,
    # but the underlying SegmentHazardContext type is shared -- see
    # app/core/weather_factor.py's module docstring).
    from app.config import DEFAULT_RAINFALL_OBSERVATION_DATE

    rain_context = rainfall_segment_context(segments, DEFAULT_RAINFALL_OBSERVATION_DATE)

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, [blocked_id])
    hazard_context = combine_active_hazards_into_segment_context([event])

    # The live API endpoints (routes_routing.py) build segment_context
    # exclusively from combine_active_hazards_into_segment_context() -- real
    # rainfall is never substituted in place of it. Confirm that context
    # alone still reports the blockage correctly regardless of what a
    # separately-computed rainfall context for the same segment would say.
    assert hazard_context[blocked_id].closed is True
    assert blocked_id not in rain_context or rain_context[blocked_id].closed is False  # rainfall never sets closed=True
    assert hazard_context[blocked_id].incident_factor == 1.0


# ---------------------------------------------------------------------------
# 11. /weather/corridor?date=2023-01-11 returns 200 with real IMD data.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    from app.store.state_store import state_store as store

    store.load()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


def test_11_weather_corridor_returns_200_for_2023_01_11(client):
    resp = client.get("/weather/corridor", params={"date": "2023-01-11"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["observation_date"] == "2023-01-11"
    assert len(body["locations"]) == 7
    for loc in body["locations"]:
        assert loc["status"] in ("ok", "missing_value", "no_coverage")


def test_weather_corridor_route_is_actually_registered_in_the_app():
    """Confirms the FastAPI route table itself, not just one lucky request --
    guards against a future router-mounting regression."""
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/weather/corridor" in paths
    assert "/weather/rainfall" in paths
    assert "/weather/segments/{segment_id}" in paths


def test_full_bhalukpong_bomdila_reroute_via_the_real_http_api(client):
    """End-to-end via the exact same HTTP surface the frontend calls --
    the literal reported reproduction scenario, through the API layer."""
    segs = client.get("/segments").json()
    doimara_ids = [s["id"] for s in segs if s["name"] == "Doimara-Nichiphu"]
    assert doimara_ids

    baseline = client.post(
        "/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}
    ).json()
    assert baseline["outcome"] == "continue"
    baseline_distance = baseline["recommended_route"]["total_distance_km"]
    on_route = [sid for sid in doimara_ids if sid in baseline["recommended_route"]["segment_ids"]]
    assert on_route

    hazard = client.post(
        "/hazards/simulate",
        json={"type": "road_blockage", "severity": "blocking", "affected_segment_ids": on_route},
    ).json()
    assert hazard["active"] is True

    disrupted = client.post(
        "/routes/evaluate-disruption",
        json={
            "origin": "Bhalukpong",
            "destination": "Bomdila",
            "previous_route_id": baseline["recommended_route"]["route_id"],
        },
    ).json()
    assert disrupted["outcome"] == "reroute"
    assert disrupted["recommended_route"] is not None
    assert disrupted["recommended_route"]["total_distance_km"] != baseline_distance
    assert set(disrupted["recommended_route"]["segment_ids"]).isdisjoint(on_route)

    client.post(f"/hazards/{hazard['id']}/clear")
    from app.store.state_store import state_store as store

    store.reset_hazards()
