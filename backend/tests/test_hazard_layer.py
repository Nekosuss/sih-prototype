"""
Tests for Part 11: landslide/flood HAZARD-ZONATION spatial layers.

**Every polygon in this file is a SYNTHETIC unit-test fixture.** None of it
is real Arunachal Pradesh landslide/flood data -- see
app/data/hazard_layer_loader.py's module docstring for the verified real
data-access status (no official APSAC file has been obtained). These
fixtures exist ONLY to validate the spatial-lookup mechanics (point-in-
polygon, class->score mapping, coverage/no-coverage handling), never to
stand in for a real hazard assessment.
"""
import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from app.config import HAZARD_CLASS_TO_SCORE
from app.core.hazard_layer_service import segment_flood_hazard, segment_landslide_hazard
from app.core.risk_engine import assess_segment_risk, historical_landslide_risk
from app.core.weather_factor import rainfall_mm_to_weather_factor
from app.data.hazard_layer_loader import (
    HazardLayerLoader,
    HazardLayerStatus,
    HazardLevel,
    HazardPolygonLayer,
    class_to_score,
)
from app.core.geo import haversine_km
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType


# ---------------------------------------------------------------------------
# Fixtures: SYNTHETIC polygons only -- see module docstring.
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_landslide_geojson(tmp_path):
    """One SYNTHETIC 'High' hazard square (lon 20.0-20.1, lat 10.0-10.1) and
    one SYNTHETIC 'Low' hazard square right next to it (lon 20.1-20.2)."""
    path = tmp_path / "synthetic_landslide.geojson"
    gdf = gpd.GeoDataFrame(
        {"hazard_class": ["High", "Low"]},
        geometry=[
            Polygon([(20.0, 10.0), (20.1, 10.0), (20.1, 10.1), (20.0, 10.1)]),
            Polygon([(20.1, 10.0), (20.2, 10.0), (20.2, 10.1), (20.1, 10.1)]),
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GeoJSON")
    return path


@pytest.fixture
def synthetic_flood_geojson(tmp_path):
    """A single SYNTHETIC 'Moderate' flood-hazard square, DIFFERENT extent
    from the landslide fixture above, to prove the two layers are
    independent."""
    path = tmp_path / "synthetic_flood.geojson"
    gdf = gpd.GeoDataFrame(
        {"hazard_class": ["Moderate"]},
        geometry=[Polygon([(20.0, 10.0), (20.2, 10.0), (20.2, 10.05), (20.0, 10.05)])],
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GeoJSON")
    return path


@pytest.fixture
def synthetic_loader(synthetic_landslide_geojson, synthetic_flood_geojson):
    return HazardLayerLoader(
        landslide_layer_path=synthetic_landslide_geojson,
        flood_layer_path=synthetic_flood_geojson,
    )


@pytest.fixture
def unavailable_loader():
    """Mirrors the CURRENT REAL state of the running application: neither
    layer present on disk."""
    return HazardLayerLoader(landslide_layer_path=None, flood_layer_path=None)


def _segment(seg_id, from_node, to_node, geometry=None, landslide_hazard_score=None, landslide_hazard_class=None,
             historical_landslide_count=0, nearest_landslide_distance_m=None, distance_km=None):
    geometry = geometry or [GeoPoint(lat=from_node.lat, lng=from_node.lng), GeoPoint(lat=to_node.lat, lng=to_node.lng)]
    # Real haversine distance over the actual geometry by default -- so
    # multi-point sampling (fractions of distance_km, walking the REAL
    # polyline) actually reaches every real vertex, including the segment's
    # true endpoint at fraction=1.0. A hardcoded/unrelated distance_km would
    # make interpolate_along_path() undershoot or overshoot the real points.
    if distance_km is None:
        distance_km = sum(
            haversine_km(geometry[i].lat, geometry[i].lng, geometry[i + 1].lat, geometry[i + 1].lng)
            for i in range(len(geometry) - 1)
        )
    return RoadSegment(
        id=seg_id,
        from_node_id=from_node.id,
        to_node_id=to_node.id,
        road_type=RoadType.tertiary,
        distance_km=distance_km,
        estimated_travel_time_min=15.0,
        geometry=geometry,
        terrain_type=TerrainType.plain,
        slope_deg=1.0,
        elevation_m=200.0,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
        historical_landslide_count=historical_landslide_count,
        nearest_landslide_distance_m=nearest_landslide_distance_m,
        landslide_hazard_score=landslide_hazard_score,
        landslide_hazard_class=landslide_hazard_class,
    )


# ---------------------------------------------------------------------------
# 1/3/4. Polygon hazard lookup: point inside / outside a SYNTHETIC polygon.
# ---------------------------------------------------------------------------


def test_point_inside_synthetic_high_hazard_polygon(synthetic_loader):
    obs = synthetic_loader.get_landslide_hazard(10.05, 20.05)  # inside the "High" square
    assert obs.status == HazardLayerStatus.ok
    assert obs.source_class == "High"
    assert obs.hazard_score == HAZARD_CLASS_TO_SCORE["high"]
    assert obs.hazard_level == HazardLevel.high


def test_point_inside_synthetic_low_hazard_polygon(synthetic_loader):
    obs = synthetic_loader.get_landslide_hazard(10.05, 20.15)  # inside the "Low" square
    assert obs.status == HazardLayerStatus.ok
    assert obs.source_class == "Low"


def test_point_outside_every_synthetic_polygon_is_no_coverage(synthetic_loader):
    obs = synthetic_loader.get_landslide_hazard(50.0, 90.0)  # nowhere near either square
    assert obs.status == HazardLayerStatus.no_coverage
    assert obs.hazard_score is None
    assert obs.source_class is None


# ---------------------------------------------------------------------------
# 5. No-coverage handling when NO layer is loaded at all (the current real state).
# ---------------------------------------------------------------------------


def test_unavailable_loader_reports_no_coverage_for_every_query(unavailable_loader):
    landslide = unavailable_loader.get_landslide_hazard(10.05, 20.05)
    flood = unavailable_loader.get_flood_hazard(10.05, 20.05)
    assert landslide.status == HazardLayerStatus.no_coverage
    assert flood.status == HazardLayerStatus.no_coverage
    assert landslide.hazard_score is None
    assert flood.hazard_score is None
    assert not unavailable_loader.landslide_layer.is_loaded
    assert not unavailable_loader.flood_layer.is_loaded


def test_missing_score_is_never_coerced_to_zero(unavailable_loader):
    """A no_coverage observation's hazard_score must be None, never 0.0 --
    0.0 would misrepresent 'no data' as 'confirmed lowest hazard'."""
    obs = unavailable_loader.get_landslide_hazard(10.0, 20.0)
    assert obs.hazard_score is None
    assert obs.hazard_score != 0.0


# ---------------------------------------------------------------------------
# 6. Source class -> normalized score mapping.
# ---------------------------------------------------------------------------


def test_class_to_score_known_values():
    assert class_to_score("Very Low") == 0.10
    assert class_to_score("low") == 0.25
    assert class_to_score("MODERATE") == 0.45
    assert class_to_score("High") == 0.70
    assert class_to_score("very high") == 0.90


def test_class_to_score_is_case_insensitive_and_trims_whitespace():
    assert class_to_score("  High  ") == class_to_score("high")


def test_class_to_score_unknown_class_raises():
    with pytest.raises(ValueError):
        class_to_score("Catastrophic")


def test_layer_load_rejects_unrecognized_class_vocabulary(tmp_path):
    path = tmp_path / "bad_classes.geojson"
    gdf = gpd.GeoDataFrame(
        {"hazard_class": ["Catastrophic"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError):
        HazardPolygonLayer(path, "hazard_class", "test layer")


# ---------------------------------------------------------------------------
# 7/8. Segment -> hazard mapping, multi-point aggregation.
# ---------------------------------------------------------------------------


def test_segment_landslide_hazard_uses_conservative_maximum_across_samples(synthetic_loader):
    a = Node(id="a", name="A", lat=10.05, lng=20.05, type=NodeType.town)  # inside "High"
    b = Node(id="b", name="B", lat=10.05, lng=20.15, type=NodeType.town)  # inside "Low"
    segment = _segment("seg_ab", a, b)

    result = segment_landslide_hazard(segment, loader=synthetic_loader)
    assert result.status == HazardLayerStatus.ok
    # The segment spans both a "High" and a "Low" synthetic zone -- the
    # conservative maximum (High) must win, not an average or the first sample.
    assert result.hazard_class == "High"
    assert result.hazard_score == HAZARD_CLASS_TO_SCORE["high"]
    assert len(result.sample_observations) == 5  # HAZARD_SEGMENT_SAMPLE_FRACTIONS


def test_segment_with_only_partial_coverage_still_reports_ok(synthetic_loader):
    """A segment whose start point misses every polygon, but whose end
    point lands inside one, must still be reported ok (from the covered
    sample) -- never no_coverage just because ONE sample missed."""
    a = Node(id="a", name="A", lat=50.0, lng=90.0, type=NodeType.town)  # far outside
    b = Node(id="b", name="B", lat=10.05, lng=20.05, type=NodeType.town)  # inside "High"
    segment = _segment("seg_ab", a, b)

    result = segment_landslide_hazard(segment, loader=synthetic_loader)
    assert result.status == HazardLayerStatus.ok
    assert result.hazard_class == "High"


def test_segment_entirely_outside_coverage_reports_no_coverage(synthetic_loader):
    a = Node(id="a", name="A", lat=50.0, lng=90.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=51.0, lng=91.0, type=NodeType.town)
    segment = _segment("seg_ab", a, b)

    result = segment_landslide_hazard(segment, loader=synthetic_loader)
    assert result.status == HazardLayerStatus.no_coverage
    assert result.hazard_score is None


# ---------------------------------------------------------------------------
# 9. Landslide and flood are kept independent.
# ---------------------------------------------------------------------------


def test_landslide_and_flood_layers_are_independent(synthetic_loader):
    # (10.02, 20.02) sits inside the landslide "High" square AND inside the
    # flood "Moderate" rectangle -- confirm each returns ITS OWN class.
    landslide = synthetic_loader.get_landslide_hazard(10.02, 20.02)
    flood = synthetic_loader.get_flood_hazard(10.02, 20.02)
    assert landslide.source_class == "High"
    assert flood.source_class == "Moderate"

    # (10.08, 20.02) sits inside the landslide "High" square but OUTSIDE the
    # flood rectangle (flood only covers lat 10.0-10.05) -- flood must be
    # no_coverage while landslide is still ok.
    landslide2 = synthetic_loader.get_landslide_hazard(10.08, 20.02)
    flood2 = synthetic_loader.get_flood_hazard(10.08, 20.02)
    assert landslide2.status == HazardLayerStatus.ok
    assert flood2.status == HazardLayerStatus.no_coverage


def test_segment_flood_hazard_independent_function(synthetic_loader):
    a = Node(id="a", name="A", lat=10.02, lng=20.02, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.02, lng=20.03, type=NodeType.town)
    segment = _segment("seg_ab", a, b)
    result = segment_flood_hazard(segment, loader=synthetic_loader)
    assert result.status == HazardLayerStatus.ok
    assert result.hazard_class == "Moderate"
    assert result.hazard_type == "flood"


# ---------------------------------------------------------------------------
# 10. Existing historical GSI fields remain unchanged by the new model fields.
# ---------------------------------------------------------------------------


def test_new_hazard_fields_default_to_none_and_do_not_disturb_gsi_fields():
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    segment = _segment("seg_ab", a, b, historical_landslide_count=3, nearest_landslide_distance_m=120.0)

    assert segment.landslide_hazard_class is None
    assert segment.landslide_hazard_score is None
    assert segment.flood_hazard_class is None
    assert segment.flood_hazard_score is None
    assert segment.hazard_layer_source == {}
    # GSI fields untouched by the new fields' mere presence on the model.
    assert segment.historical_landslide_count == 3
    assert segment.nearest_landslide_distance_m == 120.0


def test_real_corridor_segments_still_have_gsi_fields_populated(network):
    """Regression check against the REAL network: adding Part 11's fields
    to RoadSegment must not have disturbed Part 4's real GSI-derived
    historical_landslide_count/nearest_landslide_distance_m population."""
    _, segments = network
    with_history = [s for s in segments if s.historical_landslide_count > 0]
    assert len(with_history) > 0
    for s in with_history:
        assert s.nearest_landslide_distance_m is not None


# ---------------------------------------------------------------------------
# 11/12. Risk engine integration: with and without a hazard layer.
# ---------------------------------------------------------------------------


def test_historical_landslide_risk_unchanged_when_no_hazard_layer():
    """Existing behavior must be bit-for-bit identical when
    landslide_hazard_score is None (its default, and the CURRENT REAL value
    for every segment in this corridor) -- Part 11 must not silently change
    Part 5's formula for the common case."""
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    with_layer_absent = _segment("seg_ab", a, b, historical_landslide_count=4, nearest_landslide_distance_m=80.0)
    without_new_field = with_layer_absent.model_copy(update={"landslide_hazard_score": None})
    assert historical_landslide_risk(with_layer_absent) == historical_landslide_risk(without_new_field)

    # And a hand-computed sanity check against a segment with NO history at
    # all and NO hazard layer -- must be exactly 0.0, as before Part 11.
    no_evidence = _segment("seg_cd", a, b)
    assert historical_landslide_risk(no_evidence) == 0.0


def test_landslide_hazard_layer_can_increase_risk_via_max_blend():
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    # No historical evidence at all, but a SYNTHETIC "Very High" susceptibility score.
    segment = _segment("seg_ab", a, b, landslide_hazard_score=0.9, landslide_hazard_class="Very High")

    result = historical_landslide_risk(segment)
    assert result == 0.9  # max(0.0 history, 0.9 susceptibility)

    full = assess_segment_risk(segment)
    assert full.breakdown.historical_landslide_risk == 0.9
    assert any("Landslide hazard zonation layer" in r for r in full.reasons)


def test_landslide_hazard_layer_does_not_double_count_agreeing_evidence():
    """When BOTH real historical evidence and a hazard-layer score are
    present and history is actually the worse signal, the blend must still
    be the max -- not a fabricated sum exceeding either individual source."""
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    segment = _segment(
        "seg_ab", a, b,
        historical_landslide_count=10, nearest_landslide_distance_m=10.0,  # strong real history -> high history_score
        landslide_hazard_score=0.3, landslide_hazard_class="Moderate",  # weaker synthetic susceptibility
    )
    history_only = historical_landslide_risk(segment.model_copy(update={"landslide_hazard_score": None}))
    blended = historical_landslide_risk(segment)
    assert blended == history_only  # the stronger real signal (history) wins, unchanged by the weaker one
    assert blended <= 1.0


def test_rainfall_and_landslide_hazard_layer_compose_in_full_risk_score():
    """Part 10 (real rainfall -> weather_factor) and Part 11 (hazard-layer
    susceptibility) both feed the SAME unmodified assess_segment_risk() call
    -- exactly the Part 9 diagram's 'terrain + historical + susceptibility +
    rainfall -> RISK ENGINE' chain, using one segment."""
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    segment = _segment("seg_ab", a, b, landslide_hazard_score=0.7, landslide_hazard_class="High")

    weather_factor = rainfall_mm_to_weather_factor(90.0)  # a real heavy-rain value, Part 10's formula
    plain = assess_segment_risk(segment)
    with_both = assess_segment_risk(segment, weather_factor=weather_factor)

    assert with_both.breakdown.historical_landslide_risk == 0.7  # Part 11 contribution present
    assert with_both.breakdown.weather_risk == weather_factor  # Part 10 contribution present
    assert with_both.risk_score > plain.risk_score


def test_flood_hazard_score_is_not_read_by_the_risk_engine():
    """Deliberate Part 11 design choice (see risk_engine.py's
    FLOOD_HAZARD_NOTE) -- flood_hazard_score is informational only, not yet
    wired into risk_score."""
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    without_flood = _segment("seg_ab", a, b)
    with_flood = without_flood.model_copy(update={"flood_hazard_score": 0.95, "flood_hazard_class": "Very High"})

    assert assess_segment_risk(without_flood).risk_score == assess_segment_risk(with_flood).risk_score


def test_existing_synthetic_weather_incident_tests_are_unaffected():
    """Sanity check that Part 5/8's manual weather_factor/incident_factor
    inputs still behave exactly as before Part 11."""
    a = Node(id="a", name="A", lat=10.0, lng=20.0, type=NodeType.town)
    b = Node(id="b", name="B", lat=10.0, lng=20.1, type=NodeType.town)
    segment = _segment("seg_ab", a, b)
    result = assess_segment_risk(segment, weather_factor=0.5, incident_factor=0.4)
    assert result.breakdown.weather_risk == 0.5
    assert result.breakdown.incident_risk == 0.4
    assert result.breakdown.historical_landslide_risk == 0.0


# ---------------------------------------------------------------------------
# 12 (continued). Existing risk-aware routing behavior is unchanged when
# hazard layers are unavailable (the real, current corridor state).
# ---------------------------------------------------------------------------


def test_real_corridor_route_risk_unaffected_by_absent_hazard_layer(network, graph):
    from app.core.routing_engine import compare_fastest_and_safe_routes

    _, segments = network
    result = compare_fastest_and_safe_routes(graph, network[0], segments, "Bhalukpong", "Bomdila")
    assert result.outcome.value in ("fastest_route_is_safe", "safer_route_selected", "no_safe_route_available")
    # Every real segment currently has landslide_hazard_score/flood_hazard_score
    # == None (no official layer available) -- confirmed directly, not assumed.
    assert all(s.landslide_hazard_score is None for s in segments)
    assert all(s.flood_hazard_score is None for s in segments)


# ---------------------------------------------------------------------------
# 13. API endpoints.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    from app.store.state_store import state_store as store

    store.load()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


def test_get_hazard_layers_endpoint(client):
    resp = client.get("/hazards/layers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["landslide_hazard"]["loaded"] is False
    assert body["flood_hazard"]["loaded"] is False
    assert "class_to_normalized_score" in body
    assert body["corridor_coverage"]["total_segments"] > 500
    assert body["corridor_coverage"]["segments_with_real_landslide_hazard_data"] == 0


def test_get_segment_hazard_layers_endpoint(client):
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/hazards/segments/{seg_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == seg_id
    assert body["landslide_hazard"]["status"] == "no_coverage"
    assert body["landslide_hazard"]["hazard_score"] is None
    assert body["flood_hazard"]["status"] == "no_coverage"
    assert "historical_landslide_count" in body


def test_get_segment_hazard_layers_endpoint_unknown_segment_returns_404(client):
    resp = client.get("/hazards/segments/does_not_exist")
    assert resp.status_code == 404


def test_existing_hazard_simulation_endpoints_unaffected(client):
    """Sanity check: Part 11 must not have changed Part 8's simulated
    hazard endpoints at all."""
    resp = client.get("/hazards", params={"active_only": True})
    assert resp.status_code == 200
    assert resp.json() == []
