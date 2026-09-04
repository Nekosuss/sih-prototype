"""
Tests for the GSI landslide -> OSM road segment spatial join
(app/data/landslide_mapper.py).

Uses a small synthetic road network (never touching the real corridor
network or the real GSI CSV) so distances/counts are exactly known and
verifiable, plus one check against the real dataset for the pipeline's
overall shape.
"""
import pandas as pd
import pytest
from pyproj import Geod

from app.data.landslide_mapper import (
    REQUIRED_GSI_COLUMNS,
    aggregate_to_road_segments,
    landslides_to_geodataframe,
    load_gsi_csv,
    spatial_join_landslides_to_segments,
)
from app.models.network import GeoPoint, RoadSegment, RoadType, TerrainType

GEOD = Geod(ellps="WGS84")


def _segment(seg_id, lat1, lng1, lat2, lng2, name=None):
    return RoadSegment(
        id=seg_id,
        from_node_id=f"{seg_id}_a",
        to_node_id=f"{seg_id}_b",
        name=name,
        road_type=RoadType.tertiary,
        distance_km=1.0,
        estimated_travel_time_min=2.0,
        geometry=[GeoPoint(lat=lat1, lng=lng1), GeoPoint(lat=lat2, lng=lng2)],
        terrain_type=TerrainType.hill,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.1,
        current_risk_score=0.1,
    )


def _gsi_row(slide_no, lat, lng, slide_id=None):
    return {
        "slide_no": slide_no,
        "slide_id": slide_id or f"TEST/{slide_no}",
        "state": "Arunachal Pradesh",
        "description_before_coordinates": "synthetic test record",
        "latitude": lat,
        "longitude": lng,
        "description_after_coordinates": "Debris Slide NA",
        "raw_record": "synthetic",
    }


def _offset_point(lat, lng, distance_m, bearing_deg):
    """A point exactly distance_m meters from (lat, lng) at bearing_deg,
    computed with a real geodesic (not a rough degrees-per-km guess)."""
    lng2, lat2, _ = GEOD.fwd(lng, lat, bearing_deg, distance_m)
    return lat2, lng2


# A ~1.1km north-south road segment near Bhalukpong (real corridor area, so
# UTM-zone estimation behaves the same as it would for the live dataset).
SEG_LAT1, SEG_LNG1 = 27.000, 92.600
SEG_LAT2, SEG_LNG2 = 27.010, 92.600
MID_LAT, MID_LNG = 27.005, 92.600


@pytest.fixture
def two_segment_network():
    near_segment = _segment("seg_near", SEG_LAT1, SEG_LNG1, SEG_LAT2, SEG_LNG2, name="Test Road")
    # A second segment far away (~50km east), used as the "no landslides nearby" case.
    far_lat, far_lng = _offset_point(MID_LAT, MID_LNG, 50_000, 90)
    far_segment = _segment("seg_far", far_lat, far_lng, far_lat + 0.01, far_lng, name="Empty Road")
    return [near_segment, far_segment]


# ---------------------------------------------------------------------------
# 1. A landslide very close to a segment -> MATCHED.
# ---------------------------------------------------------------------------

def test_close_landslide_is_matched(two_segment_network):
    close_lat, close_lng = _offset_point(MID_LAT, MID_LNG, 20, 90)  # 20m east of the line
    df = pd.DataFrame([_gsi_row(1, close_lat, close_lng)])

    result = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)

    row = result.iloc[0]
    assert row["match_status"] == "MATCHED"
    assert row["matched_segment_id"] == "seg_near"
    assert row["matched_road_name"] == "Test Road"
    assert row["matched_highway_class"] == "tertiary"
    assert row["distance_to_road_m"] == pytest.approx(20, abs=2)


# ---------------------------------------------------------------------------
# 2. A landslide more than 500m away -> UNMATCHED (kept, not dropped).
# ---------------------------------------------------------------------------

def test_far_landslide_is_unmatched_but_kept(two_segment_network):
    far_lat, far_lng = _offset_point(MID_LAT, MID_LNG, 2000, 90)  # 2km east
    df = pd.DataFrame([_gsi_row(2, far_lat, far_lng)])

    result = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)

    assert len(result) == 1  # kept, not silently discarded
    row = result.iloc[0]
    assert row["match_status"] == "UNMATCHED"
    assert pd.isna(row["matched_segment_id"])
    assert row["distance_to_road_m"] == pytest.approx(2000, rel=0.02)  # true distance still reported


def test_match_threshold_boundary(two_segment_network):
    """400m is within the default 500m threshold; a custom 100m threshold
    makes the same point UNMATCHED — the threshold is actually applied, not
    hardcoded."""
    lat, lng = _offset_point(MID_LAT, MID_LNG, 400, 90)
    df = pd.DataFrame([_gsi_row(3, lat, lng)])

    default_result = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)
    assert default_result.iloc[0]["match_status"] == "MATCHED"

    strict_result = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=100)
    assert strict_result.iloc[0]["match_status"] == "UNMATCHED"


# ---------------------------------------------------------------------------
# 3. Multiple landslides near the same segment -> count is correct.
# ---------------------------------------------------------------------------

def test_multiple_landslides_near_same_segment_count_correctly(two_segment_network):
    points = [_offset_point(MID_LAT, MID_LNG, d, 90) for d in (10, 30, 60)]
    df = pd.DataFrame([_gsi_row(i + 10, lat, lng) for i, (lat, lng) in enumerate(points)])

    mapped = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)
    assert (mapped["match_status"] == "MATCHED").all()
    assert (mapped["matched_segment_id"] == "seg_near").all()

    features = aggregate_to_road_segments(mapped, two_segment_network)
    near_row = features[features["segment_id"] == "seg_near"].iloc[0]
    assert near_row["historical_landslide_count"] == 3
    assert near_row["nearest_landslide_distance_m"] == pytest.approx(10, abs=2)


def test_landslides_are_not_deduplicated(two_segment_network):
    """Two GSI records at (nearly) the same physical point both count —
    they're distinct observations in the source inventory."""
    lat, lng = _offset_point(MID_LAT, MID_LNG, 15, 90)
    df = pd.DataFrame([_gsi_row(20, lat, lng), _gsi_row(21, lat, lng)])

    mapped = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)
    features = aggregate_to_road_segments(mapped, two_segment_network)
    near_row = features[features["segment_id"] == "seg_near"].iloc[0]
    assert near_row["historical_landslide_count"] == 2


# ---------------------------------------------------------------------------
# 4. A road segment with no landslides -> count = 0, distance = null.
# ---------------------------------------------------------------------------

def test_segment_with_no_landslides_has_zero_count(two_segment_network):
    close_lat, close_lng = _offset_point(MID_LAT, MID_LNG, 20, 90)
    df = pd.DataFrame([_gsi_row(30, close_lat, close_lng)])

    mapped = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)
    features = aggregate_to_road_segments(mapped, two_segment_network)

    far_row = features[features["segment_id"] == "seg_far"].iloc[0]
    assert far_row["historical_landslide_count"] == 0
    assert pd.isna(far_row["nearest_landslide_distance_m"])


def test_every_segment_appears_in_features_even_with_no_landslides_at_all(two_segment_network):
    """aggregate_to_road_segments() must list every current road segment,
    not just ones with a match — tested directly against an empty mapped
    dataframe (no join needed to exercise this)."""
    empty_mapped = pd.DataFrame(columns=[
        "match_status", "matched_segment_id", "distance_to_road_m",
    ])
    features = aggregate_to_road_segments(empty_mapped, two_segment_network)
    assert set(features["segment_id"]) == {"seg_near", "seg_far"}
    assert (features["historical_landslide_count"] == 0).all()
    assert features["nearest_landslide_distance_m"].isna().all()


# ---------------------------------------------------------------------------
# 5. Coordinates are interpreted correctly as longitude/latitude.
# ---------------------------------------------------------------------------

def test_landslide_point_uses_longitude_as_x_and_latitude_as_y():
    df = pd.DataFrame([_gsi_row(40, lat=27.5, lng=91.9)])
    gdf = landslides_to_geodataframe(df)
    point = gdf.geometry.iloc[0]
    assert point.x == pytest.approx(91.9)  # x = longitude
    assert point.y == pytest.approx(27.5)  # y = latitude


# ---------------------------------------------------------------------------
# 6. Distance is calculated in meters using a projected CRS (precise check,
# not just "close" vs "far").
# ---------------------------------------------------------------------------

def test_distance_is_precise_and_metric(two_segment_network):
    lat, lng = _offset_point(SEG_LAT1, SEG_LNG1, 100, 90)  # 100m east of the segment's own endpoint
    df = pd.DataFrame([_gsi_row(50, lat, lng)])

    result = spatial_join_landslides_to_segments(df, two_segment_network, threshold_m=500)
    # A raw EPSG:4326 "distance" would be ~0.0009 (degrees) — nowhere near
    # 100. Getting ~100 back confirms the join actually reprojected to a
    # metric CRS rather than treating degrees as meters.
    assert result.iloc[0]["distance_to_road_m"] == pytest.approx(100, rel=0.05)


# ---------------------------------------------------------------------------
# Loading/validating the raw GSI CSV.
# ---------------------------------------------------------------------------

def test_load_gsi_csv_validates_columns(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("slide_no,latitude,longitude\n1,27.0,92.0\n")
    with pytest.raises(ValueError, match="missing expected columns"):
        load_gsi_csv(bad_csv)


def test_load_gsi_csv_validates_coordinate_ranges(tmp_path):
    bad_csv = tmp_path / "bad_coords.csv"
    header = ",".join(REQUIRED_GSI_COLUMNS)
    bad_csv.write_text(f"{header}\n1,X,S,before,999,92.0,after,raw\n")
    with pytest.raises(ValueError, match="latitude"):
        load_gsi_csv(bad_csv)


def test_load_real_gsi_csv_smoke():
    """The actual corridor CSV loads and validates cleanly."""
    df = load_gsi_csv()
    assert len(df) > 0
    assert df["latitude"].between(-90, 90).all()
    assert df["longitude"].between(-180, 180).all()


# ---------------------------------------------------------------------------
# Real dataset: shape/coherence check (not exact numbers, which would be
# brittle against any future re-extraction of either dataset).
# ---------------------------------------------------------------------------

def test_real_pipeline_produces_plausible_results(network):
    nodes, segments = network
    landslides_df = load_gsi_csv()
    mapped = spatial_join_landslides_to_segments(landslides_df, segments, threshold_m=500)
    features = aggregate_to_road_segments(mapped, segments)

    assert len(mapped) == len(landslides_df)  # nothing dropped
    assert set(mapped["match_status"]) <= {"MATCHED", "UNMATCHED"}
    assert (mapped["match_status"] == "MATCHED").sum() > 0  # at least some real matches
    assert (mapped["match_status"] == "UNMATCHED").sum() > 0  # this CSV covers a wider area than our corridor

    assert len(features) == len(segments)  # every current road segment represented
    matched_distances = mapped.loc[mapped["match_status"] == "MATCHED", "distance_to_road_m"]
    assert (matched_distances <= 500).all()
