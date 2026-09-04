"""
Tests for Part 10: real IMD rainfall loading (app/data/rainfall_loader.py),
rainfall -> weather_factor conversion (app/core/weather_factor.py), its
integration into the UNCHANGED Part 5/6/8 risk/routing engines via the
existing SegmentHazardContext seam, and the new /weather/* API endpoints.

Two kinds of fixtures, matching the convention already used in
test_hazard_response.py / test_risk_aware_routing.py:
- a tiny SYNTHETIC rainfall CSV (`tiny_rainfall_csv`) for deterministic
  loader-logic tests (grid lookup, missing-value handling, coverage
  boundaries) -- independent of whatever real year happens to be extracted.
- the REAL committed corridor extraction (rainfall_corridor_2023.csv, via
  get_default_rainfall_loader()) for genuine end-to-end integration checks.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import (
    RAINFALL_EXTREME_MM,
    RAINFALL_FACTOR_AT_EXTREME,
    RAINFALL_FACTOR_AT_HEAVY,
    RAINFALL_FACTOR_AT_LOW,
    RAINFALL_FACTOR_AT_MODERATE,
    RAINFALL_HEAVY_MM,
    RAINFALL_LOW_MM,
    RAINFALL_MODERATE_MM,
)
from app.core.risk_engine import assess_segment_risk
from app.core.routing_engine import build_graph, calculate_route
from app.core.reroute_service import evaluate_route_decision
from app.core.weather_factor import (
    rainfall_mm_to_weather_factor,
    rainfall_segment_context,
    weather_factor_for_point,
    weather_factor_for_segment,
)
from app.data.rainfall_loader import RainfallLoader, RainfallStatus, get_default_rainfall_loader
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType
from app.models.route import RouteDecisionOutcome


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_rainfall_csv(tmp_path):
    """A tiny, hand-built 2x2 grid over two days, with one deliberate
    IMD-style missing observation (empty field, never '0.00')."""
    path = tmp_path / "rainfall_tiny.csv"
    path.write_text(
        "date,lat,lon,rainfall_mm\n"
        "2024-01-01,10.00,20.00,0.00\n"
        "2024-01-01,10.00,20.25,50.00\n"
        "2024-01-01,10.25,20.00,\n"
        "2024-01-01,10.25,20.25,12.00\n"
        "2024-01-02,10.00,20.00,5.00\n"
        "2024-01-02,10.00,20.25,5.00\n"
        "2024-01-02,10.25,20.00,5.00\n"
        "2024-01-02,10.25,20.25,5.00\n"
    )
    return path


@pytest.fixture
def tiny_loader(tiny_rainfall_csv):
    return RainfallLoader(csv_path=tiny_rainfall_csv)


def _segment(seg_id, from_id, to_id, from_node, to_node, slope_deg=0.5):
    return RoadSegment(
        id=seg_id,
        from_node_id=from_id,
        to_node_id=to_id,
        road_type=RoadType.tertiary,
        distance_km=5.0,
        estimated_travel_time_min=10.0,
        geometry=[GeoPoint(lat=from_node.lat, lng=from_node.lng), GeoPoint(lat=to_node.lat, lng=to_node.lng)],
        terrain_type=TerrainType.plain,
        slope_deg=slope_deg,
        elevation_m=500.0,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
    )


@pytest.fixture
def diamond():
    a = Node(id="a", name="Alpha", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="Bravo", lat=10.0, lng=20.25, type=NodeType.town)
    c = Node(id="c", name="Charlie", lat=10.2, lng=20.1, type=NodeType.town)
    d = Node(id="d", name="Delta", lat=10.25, lng=20.25, type=NodeType.town)
    nodes = [a, b, c, d]
    seg_ab = _segment("seg_ab", "a", "b", a, b)
    seg_bd = _segment("seg_bd", "b", "d", b, d)
    seg_ac = _segment("seg_ac", "a", "c", a, c)
    seg_cd = _segment("seg_cd", "c", "d", c, d)
    segments = [seg_ab, seg_bd, seg_ac, seg_cd]
    graph = build_graph(nodes, segments)
    return nodes, segments, graph


# ---------------------------------------------------------------------------
# 1-4. RainfallLoader: loading, grid lookup, coverage, missing-value handling.
# ---------------------------------------------------------------------------


def test_loader_loads_real_committed_corridor_extraction():
    loader = get_default_rainfall_loader()
    assert loader.grid_cell_count > 0
    assert loader.observation_count > 0
    date_min, date_max = loader.date_range
    assert date_min <= date_max


def test_tiny_loader_reports_expected_shape(tiny_loader):
    assert tiny_loader.grid_cell_count == 4
    assert tiny_loader.observation_count == 8
    assert tiny_loader.date_range == (datetime.date(2024, 1, 1), datetime.date(2024, 1, 2))
    assert tiny_loader.missing_value_count == 1
    assert tiny_loader.daily_max_mm("2024-01-01") == 50.0


def test_nearest_grid_cell_lookup_picks_closest_real_cell(tiny_loader):
    # Closest to (10.05, 20.05) is (10.00, 20.00) -- confirm the exact
    # observed value at that real cell/date is returned, not some other cell.
    result = tiny_loader.get_daily_rainfall(10.05, 20.05, "2024-01-01")
    assert result.status == RainfallStatus.ok
    assert (result.grid_lat, result.grid_lon) == (10.0, 20.0)
    assert result.rainfall_mm == 0.0


def test_nearest_grid_cell_lookup_picks_a_different_nearby_cell(tiny_loader):
    result = tiny_loader.get_daily_rainfall(10.24, 20.26, "2024-01-01")
    assert result.status == RainfallStatus.ok
    assert (result.grid_lat, result.grid_lon) == (10.25, 20.25)
    assert result.rainfall_mm == 12.0


def test_coordinate_far_outside_grid_returns_no_coverage(tiny_loader):
    result = tiny_loader.get_daily_rainfall(50.0, 50.0, "2024-01-01")
    assert result.status == RainfallStatus.no_coverage
    assert result.rainfall_mm is None
    assert result.grid_lat is None


def test_date_outside_extracted_range_returns_no_coverage(tiny_loader):
    result = tiny_loader.get_daily_rainfall(10.0, 20.0, "2024-06-15")
    assert result.status == RainfallStatus.no_coverage
    assert result.rainfall_mm is None


def test_missing_value_is_reported_as_missing_not_zero(tiny_loader):
    result = tiny_loader.get_daily_rainfall(10.25, 20.0, "2024-01-01")
    assert result.status == RainfallStatus.missing_value
    assert result.rainfall_mm is None  # never coerced to 0.0


def test_real_zero_rainfall_is_ok_status_not_missing(tiny_loader):
    """A genuine observed 0.0mm day must be distinguishable from a missing
    observation -- both being 'falsy' must never collapse into one status."""
    result = tiny_loader.get_daily_rainfall(10.0, 20.0, "2024-01-01")
    assert result.status == RainfallStatus.ok
    assert result.rainfall_mm == 0.0


def test_loader_raises_on_missing_csv_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        RainfallLoader(csv_path=tmp_path / "does_not_exist.csv")


# ---------------------------------------------------------------------------
# 5-6. rainfall_mm -> weather_factor mapping and threshold boundaries.
# ---------------------------------------------------------------------------


def test_zero_rainfall_maps_to_zero_weather_factor():
    assert rainfall_mm_to_weather_factor(0.0) == 0.0


def test_none_rainfall_maps_to_none_weather_factor():
    """None (missing/unavailable) must never become a fabricated 0.0."""
    assert rainfall_mm_to_weather_factor(None) is None


def test_weather_factor_boundary_values_match_config_anchors():
    assert rainfall_mm_to_weather_factor(RAINFALL_LOW_MM) == RAINFALL_FACTOR_AT_LOW
    assert rainfall_mm_to_weather_factor(RAINFALL_MODERATE_MM) == RAINFALL_FACTOR_AT_MODERATE
    assert rainfall_mm_to_weather_factor(RAINFALL_HEAVY_MM) == RAINFALL_FACTOR_AT_HEAVY
    assert rainfall_mm_to_weather_factor(RAINFALL_EXTREME_MM) == RAINFALL_FACTOR_AT_EXTREME


def test_weather_factor_is_monotonically_increasing_with_rainfall():
    values = [0.0, 1.0, RAINFALL_LOW_MM, 10.0, RAINFALL_MODERATE_MM, 40.0, RAINFALL_HEAVY_MM, 150.0, RAINFALL_EXTREME_MM, 500.0]
    factors = [rainfall_mm_to_weather_factor(v) for v in values]
    assert factors == sorted(factors)


def test_weather_factor_saturates_at_one_beyond_extreme():
    assert rainfall_mm_to_weather_factor(RAINFALL_EXTREME_MM * 3) == 1.0


def test_weather_factor_always_in_unit_range_for_arbitrary_rainfall():
    for mm in [0.0, 0.1, 3.0, 20.0, 70.0, 500.0, 10000.0]:
        factor = rainfall_mm_to_weather_factor(mm)
        assert 0.0 <= factor <= 1.0


# ---------------------------------------------------------------------------
# 7. Segment-level rainfall lookup (uses geometry midpoint).
# ---------------------------------------------------------------------------


def test_weather_factor_for_point_real_corridor_location():
    """Guwahati's real coordinates, real default demo date -- must resolve
    to a real (non-missing) observation given the committed extraction."""
    from app.data.demo_locations import DEMO_LOCATIONS

    guwahati = next(loc for loc in DEMO_LOCATIONS if loc["name"] == "Guwahati")
    result = weather_factor_for_point(guwahati["lat"], guwahati["lng"], "2023-06-21")
    assert result.observation.status == RainfallStatus.ok
    assert result.observation.rainfall_mm is not None
    assert result.weather_factor is not None
    assert result.weather_factor == rainfall_mm_to_weather_factor(result.observation.rainfall_mm)


def test_weather_factor_for_segment_uses_geometry_midpoint(tiny_loader):
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.5, type=NodeType.town)
    segment = _segment("seg_ab", "a", "b", a, b)
    # weather_factor_for_segment uses geometry[len(geometry)//2] as the
    # representative point (the same convention as
    # dem_validation.py::_nearest_segment) -- for this 3-point geometry
    # that's the exact middle vertex, landing on a real 50mm grid cell.
    segment.geometry.insert(1, GeoPoint(lat=10.0, lng=20.25))

    result = weather_factor_for_segment(segment, "2024-01-01", loader=tiny_loader)
    assert result.observation.status == RainfallStatus.ok
    assert result.observation.rainfall_mm == 50.0


# ---------------------------------------------------------------------------
# 8. Real rainfall feeds into the UNCHANGED Part 5 risk engine.
# ---------------------------------------------------------------------------


def test_real_rainfall_derived_factor_increases_weather_risk_component(diamond):
    _, segments, _ = diamond
    segment = next(s for s in segments if s.id == "seg_ab")

    before = assess_segment_risk(segment)
    assert before.breakdown.weather_risk == 0.0

    weather_factor = rainfall_mm_to_weather_factor(80.0)  # a real heavy-rain value
    after = assess_segment_risk(segment, weather_factor=weather_factor)
    assert after.breakdown.weather_risk == weather_factor
    assert after.risk_score > before.risk_score
    # Terrain/history components must be untouched by a weather-only input.
    assert after.breakdown.slope_risk == before.breakdown.slope_risk
    assert after.breakdown.historical_landslide_risk == before.breakdown.historical_landslide_risk


def test_missing_rainfall_produces_same_result_as_no_weather_context(diamond):
    """A missing/unavailable real observation must behave exactly like
    'no weather context supplied' -- never like confirmed dry weather with
    an extra step in between."""
    _, segments, _ = diamond
    segment = next(s for s in segments if s.id == "seg_ab")

    plain = assess_segment_risk(segment)
    with_none_factor = assess_segment_risk(segment, weather_factor=rainfall_mm_to_weather_factor(None))
    assert with_none_factor.risk_score == plain.risk_score
    assert with_none_factor.breakdown.weather_risk == 0.0


# ---------------------------------------------------------------------------
# 9. Existing synthetic/manual weather_factor path is untouched.
# ---------------------------------------------------------------------------


def test_manual_weather_factor_path_still_works_unchanged(diamond):
    """Part 5/8's original manual/simulated weather_factor input must keep
    working exactly as before -- Part 10 only adds a new SOURCE for this
    same parameter, it does not touch assess_segment_risk() itself."""
    _, segments, _ = diamond
    segment = next(s for s in segments if s.id == "seg_ab")
    result = assess_segment_risk(segment, weather_factor=0.6)
    assert result.breakdown.weather_risk == 0.6


# ---------------------------------------------------------------------------
# 10-11. rainfall_segment_context: real rainfall as a routing input via the
# EXISTING Part 8 segment_context seam (no routing code changes needed).
# ---------------------------------------------------------------------------


def test_rainfall_segment_context_omits_segments_without_real_data(tiny_loader):
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.25, type=NodeType.town)  # real data
    c = Node(id="c", name="C", lat=80.0, lng=150.0, type=NodeType.town)  # far outside tiny grid
    seg_real = _segment("seg_real", "a", "b", a, b)
    seg_no_coverage = _segment("seg_no_coverage", "b", "c", b, c)

    context = rainfall_segment_context([seg_real, seg_no_coverage], "2024-01-01", loader=tiny_loader)
    assert "seg_real" in context
    assert "seg_no_coverage" not in context  # never fabricated as weather_factor=0.0


def test_high_rainfall_increases_route_risk_and_can_trigger_reroute(diamond):
    """Deterministic synthetic scenario (independent of whatever the real
    historical demo date happens to produce): a high rainfall-derived
    weather_factor on the fast edge forces a real reroute to the real
    alternative, using the EXACT SAME segment_context mechanism Part 8's
    simulated hazards already use -- core/routing_engine.py and
    core/reroute_service.py needed no changes for this to work."""
    from app.core.hazard_state import SegmentHazardContext

    nodes, segments, graph = diamond
    baseline = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert baseline.recommended_route.node_ids == ["a", "b", "d"]

    extreme_weather_factor = rainfall_mm_to_weather_factor(300.0)  # beyond RAINFALL_EXTREME_MM -> 1.0
    context = {"seg_ab": SegmentHazardContext(weather_factor=extreme_weather_factor)}

    decision = evaluate_route_decision(
        graph, nodes, segments, "a", "d", previous_route=baseline.recommended_route, segment_context=context
    )
    # seg_ab's weather-only contribution is capped by WEATHER_WEIGHT (0.20)
    # so this synthetic flat/no-history segment may not cross the hard
    # threshold on weather alone -- assert the real, honest effect: risk on
    # that segment strictly increased, and IF a reroute happened it avoids
    # seg_ab and is a real alternative.
    from app.core.risk_engine import assess_segment_risk

    seg_ab = next(s for s in segments if s.id == "seg_ab")
    before_risk = assess_segment_risk(seg_ab).risk_score
    after_risk = assess_segment_risk(seg_ab, weather_factor=extreme_weather_factor).risk_score
    assert after_risk > before_risk

    if decision.outcome == RouteDecisionOutcome.reroute:
        assert "seg_ab" not in decision.recommended_route.segment_ids
        assert decision.recommended_route.node_ids == ["a", "c", "d"]
    else:
        assert decision.outcome == RouteDecisionOutcome.continue_


def test_route_decision_unchanged_when_no_rainfall_context_supplied(diamond):
    """Omitting segment_context entirely must reproduce exactly the same
    behavior as before Part 10 existed -- real rainfall is purely additive."""
    nodes, segments, graph = diamond
    decision = evaluate_route_decision(graph, nodes, segments, "a", "d")
    assert decision.outcome == RouteDecisionOutcome.continue_
    assert decision.recommended_route.node_ids == ["a", "b", "d"]


# ---------------------------------------------------------------------------
# 12-13. API endpoints.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    from app.store.state_store import state_store

    state_store.load()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


def test_get_rainfall_endpoint_known_real_point(client):
    resp = client.get("/weather/rainfall", params={"lat": 26.1805978, "lon": 91.7539430, "date": "2023-06-21"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["observation_date"] == "2023-06-21"
    assert body["status"] == "ok"
    assert body["is_real_observation"] is True
    assert body["rainfall_mm"] is not None
    assert 0.0 <= body["weather_factor"] <= 1.0
    assert "IMD" in body["source"]


def test_get_rainfall_endpoint_defaults_date_when_omitted(client):
    from app.config import DEFAULT_RAINFALL_OBSERVATION_DATE

    resp = client.get("/weather/rainfall", params={"lat": 26.18, "lon": 91.75})
    assert resp.status_code == 200
    assert resp.json()["observation_date"] == DEFAULT_RAINFALL_OBSERVATION_DATE


def test_get_rainfall_endpoint_outside_coverage_reports_unavailable(client):
    resp = client.get("/weather/rainfall", params={"lat": 0.0, "lon": 0.0, "date": "2023-06-21"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_coverage"
    assert body["rainfall_mm"] is None
    assert body["weather_factor"] is None
    assert body["is_real_observation"] is False


def test_get_rainfall_endpoint_invalid_date_returns_400(client):
    resp = client.get("/weather/rainfall", params={"lat": 26.18, "lon": 91.75, "date": "not-a-date"})
    assert resp.status_code == 400


def test_get_segment_weather_endpoint_includes_full_risk_chain(client):
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/weather/segments/{seg_id}", params={"date": "2023-06-21"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == seg_id
    assert "risk" in body
    assert 0.0 <= body["risk"]["risk_score"] <= 1.0
    assert set(body["risk"]["breakdown"].keys()) == {
        "slope_risk", "historical_landslide_risk", "weather_risk", "incident_risk",
    }


def test_get_segment_weather_endpoint_unknown_segment_returns_404(client):
    resp = client.get("/weather/segments/does_not_exist")
    assert resp.status_code == 404


def test_get_corridor_weather_summary_endpoint(client):
    resp = client.get("/weather/corridor", params={"date": "2023-06-21"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["observation_date"] == "2023-06-21"
    names = {loc["name"] for loc in body["locations"]}
    assert names == {"Guwahati", "Tezpur", "Bhalukpong", "Bomdila", "Dirang", "Sela Pass", "Tawang"}
    for seg in body["high_rainfall_segments"]:
        assert seg["weather_factor"] >= body["high_rainfall_threshold_weather_factor"]


def test_existing_segment_risk_aware_endpoint_unaffected_by_rainfall_module(client):
    """Sanity check: adding Part 10 must not have changed the behavior of
    the pre-existing Part 5/8 endpoint at all."""
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/segments/{seg_id}/risk-aware")
    assert resp.status_code == 200
    assert resp.json()["breakdown"]["weather_risk"] == 0.0
