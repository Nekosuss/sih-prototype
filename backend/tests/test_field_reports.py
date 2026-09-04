"""
Tests for Part 12 field reporting / incident intelligence:
core/geo.py's nearest-point-on-polyline matching, core/field_report_service.py
(GPS -> real segment -> HazardEvent), StateStore field-report storage, and
the /field-reports API -- including the Part 12 section 16 real-corridor
validation scenario (Bhalukpong -> Bomdila).

Two kinds of fixtures, per the same convention as test_hazard_response.py:
- SYNTHETIC graphs for scenarios that need a guaranteed, deterministic
  configuration (SUSPEND, independent-hazard resolution).
- The REAL corridor (`network`/`graph` fixtures from conftest.py) for the
  genuine GPS-matching and Bhalukpong->Bomdila demo scenario.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import FIELD_REPORT_MAX_SNAP_DISTANCE_M, INCIDENT_SEVERITY_FACTOR
from app.core.field_report_service import (
    NoNearbyRoadError,
    build_field_hazard_event,
    create_field_report,
    find_nearest_segment,
    hazard_type_for_incident,
    is_possible_duplicate,
)
from app.core.geo import nearest_point_on_polyline
from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.routing_engine import build_graph, calculate_route
from app.main import app
from app.models.field_report import FieldIncidentType, FieldReport, FieldReportStatus
from app.models.hazard import HazardSeverity, HazardType
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from app.models.route import RouteDecisionOutcome
from app.store.state_store import StateStore, state_store


def _representative_point(geometry):
    """A real point on `geometry` unlikely to be shared with another
    segment at a road junction -- an interior vertex when one exists,
    otherwise the interpolated midpoint of a 2-point segment (still exactly
    on that segment's own real line, never a fabricated location)."""
    if len(geometry) > 2:
        return geometry[len(geometry) // 2]
    p0, p1 = geometry[0], geometry[-1]
    return {"lat": (p0["lat"] + p1["lat"]) / 2, "lng": (p0["lng"] + p1["lng"]) / 2}


def _segment(seg_id, from_id, to_id, from_node, to_node, distance_km=5.0, travel_time_min=10.0):
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
        slope_deg=0.5,
        elevation_m=500.0,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
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
    a = Node(id="a", name="Alpha", lat=10.0, lng=10.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.1, lng=10.1, type=NodeType.town)
    nodes = [a, b]
    seg_ab = _segment("seg_ab", "a", "b", a, b, distance_km=5, travel_time_min=10)
    segments = [seg_ab]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


# ---------------------------------------------------------------------------
# core/geo.py: nearest_point_on_polyline
# ---------------------------------------------------------------------------


def test_nearest_point_on_polyline_exact_vertex_is_zero_distance():
    points = [(10.0, 10.0), (10.1, 10.1), (10.2, 10.0)]
    lat, lng, dist_km = nearest_point_on_polyline(10.1, 10.1, points)
    assert dist_km == pytest.approx(0.0, abs=1e-6)
    assert (lat, lng) == pytest.approx((10.1, 10.1))


def test_nearest_point_on_polyline_perpendicular_offset():
    # A straight north-south edge; a point ~1km due east should land near
    # the midpoint, not get pulled to an endpoint.
    points = [(10.0, 10.0), (10.1, 10.0)]
    lat, lng, dist_km = nearest_point_on_polyline(10.05, 10.01, points)
    assert 0.0 < dist_km < 2.0
    assert 10.0 <= lat <= 10.1


def test_nearest_point_on_polyline_clamps_beyond_endpoint():
    # Query point far past the second vertex along the line's direction --
    # nearest point must clamp to the endpoint, not extrapolate past it.
    points = [(10.0, 10.0), (10.01, 10.0)]
    lat, lng, dist_km = nearest_point_on_polyline(10.5, 10.0, points)
    assert (lat, lng) == pytest.approx((10.01, 10.0))


def test_nearest_point_on_polyline_checks_every_edge_not_just_endpoints():
    # A right-angle bend: the straight line between only the first and last
    # point (a diagonal) passes nowhere near the bend vertex, but the real
    # polyline (two real edges) passes almost exactly through it.
    points = [(10.0, 10.0), (10.0, 10.1), (10.1, 10.1)]
    query = (10.001, 10.101)

    _, _, real_dist_km = nearest_point_on_polyline(*query, points)
    _, _, naive_dist_km = nearest_point_on_polyline(*query, [points[0], points[-1]])

    assert real_dist_km < 0.5
    assert real_dist_km < naive_dist_km


# ---------------------------------------------------------------------------
# core/field_report_service.py: nearest-segment matching
# ---------------------------------------------------------------------------


def test_find_nearest_segment_picks_real_closest_segment(diamond_network):
    _, segments, _ = diamond_network
    # A point essentially on seg_ab's geometry.
    segment, distance_m = find_nearest_segment(10.1, 10.1, segments)
    assert segment.id == "seg_ab"
    assert distance_m < 50.0


def test_find_nearest_segment_empty_segments_returns_none():
    segment, distance_m = find_nearest_segment(10.0, 10.0, [])
    assert segment is None
    assert distance_m is None


def test_create_field_report_rejects_location_far_from_any_road(diamond_network):
    _, segments, _ = diamond_network
    with pytest.raises(NoNearbyRoadError):
        create_field_report(
            FieldIncidentType.landslide,
            HazardSeverity.blocking,
            latitude=0.0,
            longitude=0.0,
            description="Nowhere near the corridor",
            segments=segments,
            existing_reports=[],
        )


def test_create_field_report_accepts_location_within_snap_distance(diamond_network):
    _, segments, _ = diamond_network
    report, hazard = create_field_report(
        FieldIncidentType.landslide,
        HazardSeverity.major,
        latitude=10.1,
        longitude=10.1,
        description="Debris across the carriageway",
        segments=segments,
        existing_reports=[],
        reporter_name="Officer Rai",
    )
    assert report.segment_id == "seg_ab"
    assert report.distance_to_road_m < FIELD_REPORT_MAX_SNAP_DISTANCE_M
    assert report.status == FieldReportStatus.active
    assert report.source == "field_report"
    assert report.hazard_event_id == hazard.id
    assert report.reporter_name == "Officer Rai"
    assert hazard.affected_segment_ids == ["seg_ab"]
    assert hazard.type == HazardType.landslide
    assert hazard.incident_factor == INCIDENT_SEVERITY_FACTOR["major"]
    assert hazard.weather_factor is None
    assert "field report" in hazard.message.lower()
    # Honestly states it is NOT a Part 8 demo simulation -- never mislabeled
    # with a bare "SIMULATED ..." the way HAZARD_TYPE_LABEL prefixes Part 8
    # hazards (see app/models/hazard.py).
    assert "not a part 8 simulated demo input" in hazard.message.lower()
    assert not hazard.message.upper().startswith("SIMULATED")


# ---------------------------------------------------------------------------
# Incident-type -> HazardType mapping (app/config.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "incident_type,expected",
    [
        (FieldIncidentType.landslide, HazardType.landslide),
        (FieldIncidentType.road_blockage, HazardType.road_blockage),
        (FieldIncidentType.flooding, HazardType.road_blockage),
        (FieldIncidentType.accident, HazardType.road_blockage),
        (FieldIncidentType.fallen_tree, HazardType.road_blockage),
        (FieldIncidentType.damaged_road, HazardType.road_blockage),
        (FieldIncidentType.other, HazardType.road_blockage),
    ],
)
def test_hazard_type_for_incident_mapping(incident_type, expected):
    assert hazard_type_for_incident(incident_type) == expected


def test_blocking_field_report_of_any_mapped_type_closes_segment():
    for incident_type in FieldIncidentType:
        event = build_field_hazard_event(incident_type, HazardSeverity.blocking, "seg_x")
        context = combine_active_hazards_into_segment_context([event])
        assert context["seg_x"].closed is True, incident_type


def test_minor_field_report_does_not_close_segment():
    event = build_field_hazard_event(FieldIncidentType.road_blockage, HazardSeverity.minor, "seg_x")
    context = combine_active_hazards_into_segment_context([event])
    assert context["seg_x"].closed is False


# ---------------------------------------------------------------------------
# Duplicate detection (Part 12 section 13)
# ---------------------------------------------------------------------------


def test_second_report_same_type_and_segment_flagged_possible_duplicate(diamond_network):
    _, segments, _ = diamond_network
    first, _ = create_field_report(
        FieldIncidentType.landslide, HazardSeverity.major, 10.1, 10.1, "First report",
        segments=segments, existing_reports=[],
    )
    assert first.possible_duplicate is False

    second, _ = create_field_report(
        FieldIncidentType.landslide, HazardSeverity.major, 10.1, 10.1, "Second report, same spot",
        segments=segments, existing_reports=[first],
    )
    assert second.possible_duplicate is True
    # Both are kept -- duplicate detection never discards/merges.
    assert first.id != second.id


def test_different_incident_type_same_segment_not_flagged_duplicate(diamond_network):
    _, segments, _ = diamond_network
    first, _ = create_field_report(
        FieldIncidentType.landslide, HazardSeverity.major, 10.1, 10.1, "Landslide",
        segments=segments, existing_reports=[],
    )
    second, _ = create_field_report(
        FieldIncidentType.accident, HazardSeverity.minor, 10.1, 10.1, "Unrelated accident",
        segments=segments, existing_reports=[first],
    )
    assert second.possible_duplicate is False


def test_resolved_report_does_not_count_towards_duplicate_check():
    resolved = FieldReport(
        incident_type=FieldIncidentType.landslide,
        severity=HazardSeverity.major,
        latitude=10.1,
        longitude=10.1,
        description="old",
        segment_id="seg_ab",
        distance_to_road_m=10.0,
        status=FieldReportStatus.resolved,
    )
    assert is_possible_duplicate([resolved], FieldIncidentType.landslide, "seg_ab") is False


# ---------------------------------------------------------------------------
# StateStore: independent active reports on the same segment
# ---------------------------------------------------------------------------


def test_resolving_one_field_report_does_not_clear_another_active_hazard(diamond_network):
    _, segments, _ = diamond_network
    store = StateStore()

    report_a, hazard_a = create_field_report(
        FieldIncidentType.landslide, HazardSeverity.blocking, 10.1, 10.1, "First",
        segments=segments, existing_reports=[],
    )
    store.add_hazard(hazard_a)
    store.add_field_report(report_a)

    report_b, hazard_b = create_field_report(
        FieldIncidentType.road_blockage, HazardSeverity.major, 10.1, 10.1, "Second",
        segments=segments, existing_reports=store.get_field_reports(),
    )
    store.add_hazard(hazard_b)
    store.add_field_report(report_b)

    assert report_a.segment_id == report_b.segment_id == "seg_ab"

    store.clear_hazard(report_a.hazard_event_id)
    store.resolve_field_report(report_a.id)

    assert store.get_field_report(report_a.id).status == FieldReportStatus.resolved
    assert store.get_field_report(report_b.id).status == FieldReportStatus.active
    assert store.get_hazard(report_a.hazard_event_id).active is False
    assert store.get_hazard(report_b.hazard_event_id).active is True  # untouched

    # seg_ab still carries B's contribution.
    context = combine_active_hazards_into_segment_context(store.get_hazards(active_only=True))
    assert "seg_ab" in context
    assert context["seg_ab"].incident_factor == INCIDENT_SEVERITY_FACTOR["major"]


def test_get_field_reports_active_only_filters_resolved():
    store = StateStore()
    report = FieldReport(
        incident_type=FieldIncidentType.other,
        severity=HazardSeverity.minor,
        latitude=10.0,
        longitude=10.0,
        description="x",
        segment_id="seg_ab",
        distance_to_road_m=5.0,
    )
    store.add_field_report(report)
    assert len(store.get_field_reports(active_only=True)) == 1

    store.resolve_field_report(report.id)
    assert len(store.get_field_reports(active_only=True)) == 0
    assert len(store.get_field_reports(active_only=False)) == 1


def test_resolve_unknown_field_report_returns_none():
    store = StateStore()
    assert store.resolve_field_report("report_doesnotexist") is None


def test_resolve_already_resolved_report_is_idempotent():
    store = StateStore()
    report = FieldReport(
        incident_type=FieldIncidentType.other, severity=HazardSeverity.minor,
        latitude=10.0, longitude=10.0, description="x", segment_id="seg_ab", distance_to_road_m=5.0,
    )
    store.add_field_report(report)
    first = store.resolve_field_report(report.id)
    second = store.resolve_field_report(report.id)
    assert first.resolved_at == second.resolved_at


# ---------------------------------------------------------------------------
# Reroute integration at the engine level (mirrors test_hazard_response.py)
# ---------------------------------------------------------------------------


def test_blocking_field_report_on_current_route_forces_reroute(diamond_network):
    nodes, segments, graph = diamond_network
    baseline = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert baseline.recommended_route.node_ids == ["a", "b", "d"]

    report, hazard = create_field_report(
        FieldIncidentType.landslide, HazardSeverity.blocking, 10.1, 10.1, "Landslide on seg_ab",
        segments=segments, existing_reports=[],
    )
    context = combine_active_hazards_into_segment_context([hazard])

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "d",
        previous_route=baseline.recommended_route,
        segment_context=context,
        active_hazard_ids=[hazard.id],
    )
    assert decision.outcome == RouteDecisionOutcome.reroute
    assert "seg_ab" not in decision.recommended_route.segment_ids
    assert decision.recommended_route.node_ids == ["a", "c", "d"]


def test_blocking_field_report_on_single_access_route_suspends(single_edge_network):
    nodes, segments, graph = single_edge_network
    report, hazard = create_field_report(
        FieldIncidentType.road_blockage, HazardSeverity.blocking, 10.05, 10.05,
        "Bridge washed out", segments=segments, existing_reports=[],
    )
    context = combine_active_hazards_into_segment_context([hazard])

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "b", segment_context=context, active_hazard_ids=[hazard.id]
    )
    assert decision.outcome == RouteDecisionOutcome.suspend
    assert decision.recommended_route is None


def test_field_report_on_unrelated_segment_does_not_change_route(diamond_network):
    nodes, segments, graph = diamond_network
    baseline = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert baseline.recommended_route.node_ids == ["a", "b", "d"]

    # seg_cd (c=10.2,10.1 -> d=10.4,10.4) is not on the a->b->d route at all.
    report, hazard = create_field_report(
        FieldIncidentType.accident, HazardSeverity.blocking, 10.3, 10.25, "Accident on seg_cd",
        segments=segments, existing_reports=[],
    )
    assert report.segment_id == "seg_cd"
    context = combine_active_hazards_into_segment_context([hazard])

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "d",
        previous_route=baseline.recommended_route,
        segment_context=context,
        active_hazard_ids=[hazard.id],
    )
    assert decision.outcome == RouteDecisionOutcome.continue_
    assert decision.recommended_route.node_ids == baseline.recommended_route.node_ids


# ---------------------------------------------------------------------------
# API tests (real corridor) -- mirrors test_api.py's style/fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    state_store.load()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture
def clean_state():
    """Field-report tests mutate the shared (module-scoped) state_store via
    the API -- reset hazards/field-reports after each test so they never
    leak into each other or into unrelated tests later in this module."""
    yield
    state_store.reset_hazards()
    state_store._field_reports = {}


def test_create_field_report_api_matches_real_segment_and_creates_hazard(client, clean_state):
    segs = client.get("/segments").json()
    target = next(s for s in segs if s["geometry"])
    point = target["geometry"][len(target["geometry"]) // 2]

    resp = client.post(
        "/field-reports",
        json={
            "incident_type": "landslide",
            "severity": "major",
            "latitude": point["lat"],
            "longitude": point["lng"],
            "description": "Loose debris across half the carriageway",
            "reporter_name": "Field Officer A",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["segment_id"] == target["id"]
    assert body["report"]["distance_to_road_m"] < 5.0  # essentially on the segment
    assert body["report"]["source"] == "field_report"
    assert body["report"]["status"] == "active"
    assert body["hazard_event"]["type"] == "landslide"
    assert body["hazard_event"]["affected_segment_ids"] == [target["id"]]
    assert "not a part 8 simulated demo input" in body["hazard_event"]["message"].lower()
    assert not body["hazard_event"]["message"].upper().startswith("SIMULATED")
    assert 0.0 <= body["current_risk"]["risk_score"] <= 1.0
    assert body["route_decision"] is None  # no origin/destination supplied


def test_create_field_report_api_rejects_far_location(client, clean_state):
    resp = client.post(
        "/field-reports",
        json={
            "incident_type": "landslide",
            "severity": "major",
            "latitude": 0.0,
            "longitude": 0.0,
            "description": "Nowhere near the corridor",
        },
    )
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"latitude": 999.0}, "latitude"),
        ({"longitude": 999.0}, "longitude"),
        ({"incident_type": "meteor_strike"}, "incident_type"),
        ({"severity": "catastrophic"}, "severity"),
        ({"description": ""}, "description"),
    ],
)
def test_create_field_report_api_validation_errors(client, clean_state, payload, field):
    base = {
        "incident_type": "landslide",
        "severity": "major",
        "latitude": 27.0137235,
        "longitude": 92.6358068,
        "description": "Valid description",
    }
    base.update(payload)
    resp = client.post("/field-reports", json=base)
    assert resp.status_code == 422, field


def test_list_and_get_field_report_api(client, clean_state):
    created = client.post(
        "/field-reports",
        json={
            "incident_type": "fallen_tree",
            "severity": "minor",
            "latitude": 27.0137235,
            "longitude": 92.6358068,
            "description": "Tree branch partially blocking one lane",
        },
    ).json()["report"]

    listed = client.get("/field-reports").json()
    assert any(r["id"] == created["id"] for r in listed)

    fetched = client.get(f"/field-reports/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    missing = client.get("/field-reports/report_doesnotexist")
    assert missing.status_code == 404


def test_resolve_field_report_api_clears_hazard_and_reopens_segment(client, clean_state):
    segs = client.get("/segments").json()
    target = next(s for s in segs if s["geometry"])
    point = _representative_point(target["geometry"])

    created = client.post(
        "/field-reports",
        json={
            "incident_type": "road_blockage",
            "severity": "blocking",
            "latitude": point["lat"],
            "longitude": point["lng"],
            "description": "Fully blocked by fallen debris",
        },
    ).json()

    blocked_risk = client.get(f"/segments/{target['id']}/risk-aware").json()
    assert blocked_risk["breakdown"]["incident_risk"] == INCIDENT_SEVERITY_FACTOR["blocking"]

    resolved = client.post(f"/field-reports/{created['report']['id']}/resolve")
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["report"]["status"] == "resolved"
    assert body["hazard_event"]["active"] is False

    reverted_risk = client.get(f"/segments/{target['id']}/risk-aware").json()
    assert reverted_risk["breakdown"]["incident_risk"] == 0.0


def test_resolving_one_report_leaves_another_active_report_on_same_segment(client, clean_state):
    segs = client.get("/segments").json()
    target = next(s for s in segs if s["geometry"])
    point = _representative_point(target["geometry"])

    payload_base = {
        "latitude": point["lat"],
        "longitude": point["lng"],
        "severity": "major",
    }
    first = client.post(
        "/field-reports",
        json={**payload_base, "incident_type": "landslide", "description": "First independent report"},
    ).json()
    second = client.post(
        "/field-reports",
        json={**payload_base, "incident_type": "accident", "description": "Second independent report"},
    ).json()
    assert first["report"]["segment_id"] == second["report"]["segment_id"] == target["id"]

    client.post(f"/field-reports/{first['report']['id']}/resolve")

    active = client.get("/field-reports").json()
    assert not any(r["id"] == first["report"]["id"] for r in active)
    assert any(r["id"] == second["report"]["id"] for r in active)

    still_risky = client.get(f"/segments/{target['id']}/risk-aware").json()
    assert still_risky["breakdown"]["incident_risk"] == INCIDENT_SEVERITY_FACTOR["major"]


def test_field_report_flags_possible_duplicate_via_api(client, clean_state):
    segs = client.get("/segments").json()
    target = next(s for s in segs if s["geometry"])
    point = _representative_point(target["geometry"])
    payload = {
        "incident_type": "landslide",
        "severity": "major",
        "latitude": point["lat"],
        "longitude": point["lng"],
        "description": "Landslide report",
    }
    first = client.post("/field-reports", json=payload).json()
    second = client.post("/field-reports", json={**payload, "description": "Same landslide, different reporter"}).json()
    assert first["report"]["possible_duplicate"] is False
    assert second["report"]["possible_duplicate"] is True


# ---------------------------------------------------------------------------
# Part 12 section 16: real Bhalukpong -> Bomdila validation scenario.
# ---------------------------------------------------------------------------


def test_real_corridor_field_report_scenario_bhalukpong_bomdila(client, clean_state):
    """End-to-end: normal route -> field report (real coordinate on a real
    segment on the route) -> segment operationally blocked -> existing
    reroute logic selects a genuine alternative -> resolving restores the
    original route recommendation. Mirrors
    test_api.py::test_evaluate_disruption_real_corridor_reroutes_after_hazard,
    but driven entirely through POST /field-reports instead of
    POST /hazards/simulate."""
    baseline_calc = client.post("/routes/calculate", json={"origin": "Bhalukpong", "destination": "Bomdila"}).json()
    baseline_route = baseline_calc["route"]
    assert baseline_route["total_distance_km"] == pytest.approx(98.22, abs=0.5)

    segs_by_id = {s["id"]: s for s in client.get("/segments").json()}
    on_route_segment_id = next(
        sid for sid in baseline_route["segment_ids"] if segs_by_id[sid]["geometry"]
    )
    on_route_segment = segs_by_id[on_route_segment_id]
    # A REAL coordinate taken directly from this segment's own OSM geometry
    # -- never fabricated.
    point = on_route_segment["geometry"][len(on_route_segment["geometry"]) // 2]

    baseline_decision = client.post(
        "/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}
    ).json()
    assert baseline_decision["outcome"] == "continue"

    report_resp = client.post(
        "/field-reports",
        json={
            "incident_type": "landslide",
            "severity": "blocking",
            "latitude": point["lat"],
            "longitude": point["lng"],
            "description": "Major landslide has completely blocked the carriageway",
            "reporter_name": "Field Officer, Bhalukpong-Bomdila corridor",
            "origin": "Bhalukpong",
            "destination": "Bomdila",
            "previous_route_id": baseline_decision["recommended_route"]["route_id"],
        },
    )
    assert report_resp.status_code == 200
    body = report_resp.json()

    # GPS snapped to the exact real segment the coordinate was taken from.
    assert body["report"]["segment_id"] == on_route_segment_id
    assert body["report"]["distance_to_road_m"] < 5.0

    # Segment is now operationally blocked (blocking landslide -> closed).
    blocked_risk = client.get(f"/segments/{on_route_segment_id}/risk-aware").json()
    assert blocked_risk["breakdown"]["incident_risk"] == INCIDENT_SEVERITY_FACTOR["blocking"]

    # The bundled route_decision already reports REROUTE with a genuine
    # alternative that avoids the blocked segment.
    decision = body["route_decision"]
    assert decision is not None
    assert decision["outcome"] == "reroute"
    new_route = decision["recommended_route"]
    assert on_route_segment_id not in new_route["segment_ids"]
    real_segment_ids = set(segs_by_id.keys())
    assert set(new_route["segment_ids"]) <= real_segment_ids  # never fabricated

    # Independently re-confirm via /routes/evaluate-disruption (the same
    # existing endpoint the frontend RouteComparison view calls).
    disrupted = client.post(
        "/routes/evaluate-disruption",
        json={
            "origin": "Bhalukpong",
            "destination": "Bomdila",
            "previous_route_id": baseline_decision["recommended_route"]["route_id"],
        },
    ).json()
    assert disrupted["outcome"] == "reroute"
    assert on_route_segment_id not in disrupted["recommended_route"]["segment_ids"]

    # Resolve the report -- the segment becomes available again.
    resolve_resp = client.post(
        f"/field-reports/{body['report']['id']}/resolve",
        json={"origin": "Bhalukpong", "destination": "Bomdila"},
    )
    assert resolve_resp.status_code == 200
    resolved_body = resolve_resp.json()
    assert resolved_body["report"]["status"] == "resolved"
    assert resolved_body["route_decision"]["outcome"] == "continue"

    reverted_risk = client.get(f"/segments/{on_route_segment_id}/risk-aware").json()
    assert reverted_risk["breakdown"]["incident_risk"] == 0.0

    restored = client.post(
        "/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}
    ).json()
    assert restored["outcome"] == "continue"


# ---------------------------------------------------------------------------
# Vehicle integration (Part 9 + Part 12): existing vehicle polling picks up
# a field-report-created hazard automatically, with zero extra vehicle code.
# ---------------------------------------------------------------------------


def test_vehicle_reroutes_around_field_report_hazard_ahead(client, clean_state):
    import time

    on_route_ids = [s["id"] for s in client.get("/segments").json() if s["name"] == "Doimara-Nichiphu"]
    created = client.post("/vehicles", json={"name": "Field Truck", "origin": "Bhalukpong", "destination": "Bomdila"}).json()
    route_segment_ids = set(created["current_route"]["segment_ids"])
    affected = [sid for sid in on_route_ids if sid in route_segment_ids]
    assert affected, "expected the real route to use a real Doimara-Nichiphu segment"

    segs_by_id = {s["id"]: s for s in client.get("/segments").json()}
    point = _representative_point(segs_by_id[affected[0]]["geometry"])

    client.post(f"/vehicles/{created['id']}/start")
    report_resp = client.post(
        "/field-reports",
        json={
            "incident_type": "road_blockage",
            "severity": "blocking",
            "latitude": point["lat"],
            "longitude": point["lng"],
            "description": "Road completely blocked ahead of the vehicle",
        },
    )
    assert report_resp.status_code == 200
    blocked_segment_id = report_resp.json()["report"]["segment_id"]
    assert blocked_segment_id in affected
    time.sleep(0.5)

    polled = client.get(f"/vehicles/{created['id']}").json()
    assert polled["status"] in ("rerouting", "en_route")
    # The one real segment the field report actually blocked is avoided --
    # other same-named "Doimara-Nichiphu" segments the report never touched
    # may still legitimately appear on the new route.
    assert blocked_segment_id not in polled["current_route"]["segment_ids"]
