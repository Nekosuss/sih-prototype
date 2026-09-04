"""
Tests for Part 8 dynamic hazard response: HazardEvent construction/combination
(core/hazard_state.py), StateStore hazard storage, and the
CONTINUE/REROUTE/SUSPEND decision engine (core/reroute_service.py).

Two kinds of fixtures, per the same convention as test_risk_aware_routing.py:
- SYNTHETIC graphs (prefixed `synthetic_`/local helpers) for scenarios that
  need a guaranteed, deterministic configuration (SUSPEND, static-field
  preservation, multi-hazard combination).
- The REAL corridor (`network`/`graph` fixtures from conftest.py) for the
  genuine Tezpur/Bhalukpong->Bomdila demo scenario.
"""
import pytest

from app.config import HARD_UNSAFE_RISK_THRESHOLD, INCIDENT_SEVERITY_FACTOR, WEATHER_SEVERITY_FACTOR
from app.core.hazard_state import build_hazard_event, combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.risk_engine import assess_segment_risk
from app.core.routing_engine import build_graph, calculate_route
from app.models.hazard import HazardSeverity, HazardType
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from app.models.route import RouteDecisionOutcome
from app.store.state_store import StateStore


def _segment(
    seg_id, from_id, to_id, from_node, to_node, distance_km=5.0, travel_time_min=10.0,
    slope_deg=0.5, historical_landslide_count=0, nearest_landslide_distance_m=None,
):
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
def diamond_nodes():
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.2, lng=10.2, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=10.1, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=10.4, lng=10.4, type=NodeType.town)
    return [a, b, c, d]


@pytest.fixture
def diamond_network(diamond_nodes):
    """A-B-D: short/fast, flat, no history. A-C-D: a bit longer/slower,
    also flat/no history. Both safe under normal conditions -- a hazard is
    applied to seg_ab in individual tests to force a reroute."""
    a, b, c, d = diamond_nodes
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10)
    seg_bd = _segment("seg_bd", "b", "d", b, d, distance_km=5, travel_time_min=10)
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=6, travel_time_min=13)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=6, travel_time_min=13)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(diamond_nodes, segments)
    return diamond_nodes, segments, graph


@pytest.fixture
def single_edge_network():
    """A single a->b edge with no alternative at all -- for SUSPEND."""
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.1, type=NodeType.town)
    nodes = [a, b]
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10)
    segments = [seg_ab]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


# ---------------------------------------------------------------------------
# 1. Hazard event creation.
# ---------------------------------------------------------------------------


def test_build_hazard_event_heavy_rain_sets_weather_factor_only():
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    assert event.type == HazardType.heavy_rain
    assert event.severity == HazardSeverity.major
    assert event.weather_factor == WEATHER_SEVERITY_FACTOR["major"]
    assert event.incident_factor is None
    assert event.active is True
    assert event.id.startswith("hazard_")
    assert "SIMULATED" in event.message.upper()
    assert "not a live weather" in event.message.lower()


def test_build_hazard_event_road_blockage_sets_incident_factor_only():
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    assert event.incident_factor == INCIDENT_SEVERITY_FACTOR["blocking"]
    assert event.weather_factor is None


def test_build_hazard_event_landslide_sets_incident_factor():
    event = build_hazard_event(HazardType.landslide, HazardSeverity.minor, ["seg_ab", "seg_cd"])
    assert event.incident_factor == INCIDENT_SEVERITY_FACTOR["minor"]
    assert event.affected_segment_ids == ["seg_ab", "seg_cd"]


# ---------------------------------------------------------------------------
# 2. Hazard event affects only specified segments.
# ---------------------------------------------------------------------------


def test_segment_context_only_includes_affected_segments():
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    assert set(context.keys()) == {"seg_ab"}
    assert "seg_cd" not in context


# ---------------------------------------------------------------------------
# 3. Heavy rain increases weather contribution.
# ---------------------------------------------------------------------------


def test_heavy_rain_increases_weather_risk_component(diamond_network):
    nodes, segments, graph = diamond_network
    segment = next(s for s in segments if s.id == "seg_ab")

    before = assess_segment_risk(segment)
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    ctx = context["seg_ab"]
    after = assess_segment_risk(segment, weather_factor=ctx.weather_factor, incident_factor=ctx.incident_factor)

    assert before.breakdown.weather_risk == 0.0
    assert after.breakdown.weather_risk == WEATHER_SEVERITY_FACTOR["major"]
    assert after.risk_score > before.risk_score
    # Other components must be unaffected by a weather-only hazard.
    assert after.breakdown.slope_risk == before.breakdown.slope_risk
    assert after.breakdown.historical_landslide_risk == before.breakdown.historical_landslide_risk
    assert after.breakdown.incident_risk == 0.0


# ---------------------------------------------------------------------------
# 4. Landslide/road blockage increases incident contribution.
# ---------------------------------------------------------------------------


def test_road_blockage_increases_incident_risk_component(diamond_network):
    nodes, segments, graph = diamond_network
    segment = next(s for s in segments if s.id == "seg_ab")

    before = assess_segment_risk(segment)
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.major, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    ctx = context["seg_ab"]
    after = assess_segment_risk(segment, weather_factor=ctx.weather_factor, incident_factor=ctx.incident_factor)

    assert before.breakdown.incident_risk == 0.0
    assert after.breakdown.incident_risk == INCIDENT_SEVERITY_FACTOR["major"]
    assert after.risk_score > before.risk_score
    assert after.breakdown.weather_risk == 0.0


# ---------------------------------------------------------------------------
# 5. Static DEM/GSI fields are unchanged after hazard activation.
# ---------------------------------------------------------------------------


def test_hazard_does_not_mutate_static_segment_fields(diamond_network):
    nodes, segments, graph = diamond_network
    segment = next(s for s in segments if s.id == "seg_ab")
    original = segment.model_copy(deep=True)

    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    ctx = context["seg_ab"]
    assess_segment_risk(segment, weather_factor=ctx.weather_factor, incident_factor=ctx.incident_factor)

    # The RoadSegment object itself (static DEM/GSI/OSM fields) must be
    # byte-for-byte unchanged -- a hazard only ever produces a transient
    # SegmentHazardContext, never a mutation.
    assert segment.elevation_m == original.elevation_m
    assert segment.slope_deg == original.slope_deg
    assert segment.historical_landslide_count == original.historical_landslide_count
    assert segment.nearest_landslide_distance_m == original.nearest_landslide_distance_m
    assert segment == original


# ---------------------------------------------------------------------------
# 6 / 12. Clearing a hazard removes its dynamic effect.
# ---------------------------------------------------------------------------


def test_clearing_hazard_removes_it_from_segment_context():
    store = StateStore()
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    store.add_hazard(event)
    assert "seg_ab" in combine_active_hazards_into_segment_context(store.get_hazards(active_only=True))

    cleared = store.clear_hazard(event.id)
    assert cleared.active is False
    assert cleared.cleared_at is not None
    # The event is retained (not deleted) but no longer contributes.
    assert store.get_hazard(event.id) is not None
    assert combine_active_hazards_into_segment_context(store.get_hazards(active_only=True)) == {}


def test_clear_unknown_hazard_returns_none():
    store = StateStore()
    assert store.clear_hazard("hazard_doesnotexist") is None


def test_clear_already_cleared_hazard_is_idempotent():
    store = StateStore()
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.minor, ["seg_ab"])
    store.add_hazard(event)
    first = store.clear_hazard(event.id)
    second = store.clear_hazard(event.id)
    assert first.cleared_at == second.cleared_at


def test_reset_hazards_removes_all_history():
    store = StateStore()
    store.add_hazard(build_hazard_event(HazardType.heavy_rain, HazardSeverity.minor, ["seg_ab"]))
    store.reset_hazards()
    assert store.get_hazards() == []


# ---------------------------------------------------------------------------
# 7. Normal route remains unchanged when no hazard is active.
# ---------------------------------------------------------------------------


def test_no_hazard_decision_matches_plain_comparison(diamond_network):
    nodes, segments, graph = diamond_network
    decision = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert decision.outcome == RouteDecisionOutcome.continue_
    assert decision.recommended_route.node_ids == ["a", "b", "d"]  # the real fastest/safe path, untouched


# ---------------------------------------------------------------------------
# 8. Real hazard on a real route causes risk reassessment.
# 9 / 10. Real alternative route causes REROUTE with an actual graph route.
# ---------------------------------------------------------------------------


def test_hazard_on_diamond_forces_reroute_to_real_alternative(diamond_network):
    nodes, segments, graph = diamond_network

    baseline = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert baseline.outcome == RouteDecisionOutcome.continue_
    assert baseline.recommended_route.node_ids == ["a", "b", "d"]

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "d",
        previous_route=baseline.recommended_route,
        segment_context=context,
        active_hazard_ids=[event.id],
    )
    assert decision.outcome == RouteDecisionOutcome.reroute
    assert decision.recommended_route is not None
    assert decision.recommended_route.node_ids == ["a", "c", "d"]  # a REAL alternative from the same graph
    assert "seg_ab" not in decision.recommended_route.segment_ids
    assert decision.affected_segment_ids == ["seg_ab"]
    assert decision.active_hazard_ids == [event.id]
    assert decision.eta_change_min is not None and decision.eta_change_min > 0


def test_real_corridor_bhalukpong_bomdila_hazard_causes_reroute(network, graph):
    """Part 8 section 11's real-OSM demo scenario: block the real
    'Doimara-Nichiphu' segment (found via the actual loaded network -- its
    id is never hard-coded) with a simulated blocking road_blockage hazard
    and confirm risk-aware routing genuinely reroutes around it."""
    nodes, segments = network

    baseline_route = calculate_route(graph, nodes, segments, "Bhalukpong", "Bomdila")
    doimara_ids = {s.id for s in segments if s.name == "Doimara-Nichiphu"}
    on_route = [sid for sid in baseline_route.segment_ids if sid in doimara_ids]
    assert on_route, "expected the real baseline route to use a real Doimara-Nichiphu segment"

    baseline_decision = evaluate_route_decision(graph, nodes, segments, "Bhalukpong", "Bomdila")
    assert baseline_decision.outcome == RouteDecisionOutcome.continue_

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, on_route)
    context = combine_active_hazards_into_segment_context([event])

    # Confirm the hazard actually crosses the hard unsafe threshold (or is
    # closed outright) -- not asserting a hard-coded score, just that this
    # real segment's context now makes it infeasible.
    blocked_segment = next(s for s in segments if s.id == on_route[0])
    ctx = context[on_route[0]]
    reassessed = assess_segment_risk(blocked_segment, weather_factor=ctx.weather_factor, incident_factor=ctx.incident_factor)
    assert ctx.closed is True  # road_blockage + blocking severity => operationally closed

    decision = evaluate_route_decision(
        graph, nodes, segments, "Bhalukpong", "Bomdila",
        previous_route=baseline_decision.recommended_route,
        segment_context=context,
        active_hazard_ids=[event.id],
    )
    assert decision.outcome == RouteDecisionOutcome.reroute
    assert decision.recommended_route is not None
    assert not (set(decision.recommended_route.segment_ids) & set(on_route))  # avoids every blocked segment
    real_segment_ids = {s.id for s in segments}
    assert set(decision.recommended_route.segment_ids) <= real_segment_ids  # never fabricated


# ---------------------------------------------------------------------------
# 11. No-safe-route scenario causes SUSPEND.
# ---------------------------------------------------------------------------


def test_no_alternative_hazard_causes_suspend(single_edge_network):
    nodes, segments, graph = single_edge_network
    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "b", segment_context=context, active_hazard_ids=[event.id]
    )
    assert decision.outcome == RouteDecisionOutcome.suspend
    assert decision.recommended_route is None
    assert "no safe" in decision.reason.lower() or "suspend" in decision.reason.lower()


# ---------------------------------------------------------------------------
# 13. Repeated evaluation is deterministic.
# ---------------------------------------------------------------------------


def test_repeated_evaluation_is_deterministic(diamond_network):
    nodes, segments, graph = diamond_network
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])

    first = evaluate_route_decision(graph, nodes, segments, "a", "d", segment_context=context)
    second = evaluate_route_decision(graph, nodes, segments, "a", "d", segment_context=context)
    assert first.outcome == second.outcome
    assert first.recommended_route.node_ids == second.recommended_route.node_ids
    assert first.recommended_route_risk == second.recommended_route_risk


# ---------------------------------------------------------------------------
# 14. Invalid segment IDs are handled safely.
# ---------------------------------------------------------------------------


def test_hazard_referencing_unknown_segment_is_simply_inert(diamond_network):
    """build_hazard_event/combine_active_hazards_into_segment_context don't
    validate segment existence themselves (that's the API layer's job --
    see test_api.py) -- but an unknown id must not crash routing, it's just
    a segment_context entry nothing ever looks up."""
    nodes, segments, graph = diamond_network
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_does_not_exist"])
    context = combine_active_hazards_into_segment_context([event])

    decision = evaluate_route_decision(graph, nodes, segments, "a", "d", segment_context=context)
    assert decision.outcome == RouteDecisionOutcome.continue_
    assert decision.recommended_route.node_ids == ["a", "b", "d"]


# ---------------------------------------------------------------------------
# 15. Multiple simultaneous hazards combine correctly.
# ---------------------------------------------------------------------------


def test_multiple_hazards_on_same_segment_combine_via_max():
    minor_rain = build_hazard_event(HazardType.heavy_rain, HazardSeverity.minor, ["seg_ab"])
    major_rain = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([minor_rain, major_rain])
    assert context["seg_ab"].weather_factor == WEATHER_SEVERITY_FACTOR["major"]  # max, not sum/average


def test_multiple_hazard_types_on_same_segment_combine_independently():
    rain = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    blockage = build_hazard_event(HazardType.road_blockage, HazardSeverity.minor, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([rain, blockage])
    ctx = context["seg_ab"]
    assert ctx.weather_factor == WEATHER_SEVERITY_FACTOR["major"]
    assert ctx.incident_factor == INCIDENT_SEVERITY_FACTOR["minor"]
    assert ctx.closed is False  # minor severity road_blockage does not close


def test_inactive_hazards_do_not_contribute_to_combination():
    event = build_hazard_event(HazardType.heavy_rain, HazardSeverity.major, ["seg_ab"])
    inactive = event.model_copy(update={"active": False})
    context = combine_active_hazards_into_segment_context([inactive])
    assert context == {}


def test_closure_requires_both_closure_type_and_blocking_severity():
    minor_blockage = build_hazard_event(HazardType.road_blockage, HazardSeverity.minor, ["seg_ab"])
    blocking_rain = build_hazard_event(HazardType.heavy_rain, HazardSeverity.blocking, ["seg_cd"])
    context = combine_active_hazards_into_segment_context([minor_blockage, blocking_rain])
    assert context["seg_ab"].closed is False  # right type, wrong severity
    assert context["seg_cd"].closed is False  # right severity, wrong type (weather doesn't close roads)


def test_blocking_road_blockage_closes_segment():
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    assert context["seg_ab"].closed is True


def test_blocking_landslide_closes_segment():
    event = build_hazard_event(HazardType.landslide, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    assert context["seg_ab"].closed is True


# ---------------------------------------------------------------------------
# Hysteresis (Part 8 section 7)
# ---------------------------------------------------------------------------


def test_hysteresis_keeps_previous_route_for_marginal_improvement(diamond_nodes):
    """Both paths safe; a REAL but only marginally better alternative must
    NOT trigger a reroute (avoids flapping near a boundary)."""
    a, b, c, d = diamond_nodes
    # seg_ab: small nonzero risk (slight slope). seg_ac: negligibly lower risk.
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10, slope_deg=5.0)
    seg_bd = _segment("seg_bd", "b", "d", b, d, distance_km=5, travel_time_min=10)
    seg_ac = _segment("seg_ac", "a", "c", a, c, distance_km=5, travel_time_min=10, slope_deg=4.5)
    seg_cd = _segment("seg_cd", "c", "d", c, d, distance_km=5, travel_time_min=10)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(diamond_nodes, segments)

    previous = calculate_route(graph, diamond_nodes, segments, "a", "d")
    decision = evaluate_route_decision(graph, diamond_nodes, segments, "a", "d", previous_route=previous)
    assert decision.outcome == RouteDecisionOutcome.continue_
    assert decision.recommended_route.node_ids == previous.node_ids


def test_hysteresis_does_not_apply_when_previous_route_becomes_infeasible(diamond_network):
    """Contrast with the marginal case above: when the previous route
    actually becomes infeasible (hazard-closed), hysteresis must NOT
    prevent a reroute regardless of how small the margin looks."""
    nodes, segments, graph = diamond_network
    previous = calculate_route(graph, nodes, segments, "a", "d")
    assert previous.node_ids == ["a", "b", "d"]

    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, ["seg_ab"])
    context = combine_active_hazards_into_segment_context([event])
    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "d", previous_route=previous, segment_context=context
    )
    assert decision.outcome == RouteDecisionOutcome.reroute
