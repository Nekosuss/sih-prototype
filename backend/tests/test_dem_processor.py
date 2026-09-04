"""
Unit tests for dem_processor.py's resampling/elevation/slope math, using a
FakeDem test double instead of the real ~50MB SRTM tile set — these tests
check the MATH (given some elevation profile, is the derived slope
correct?), not the real corridor's real terrain (see
tests/test_dem_integration.py and app/data/dem_validation.py for that).
"""
import math

import pytest

from app.data.dem_processor import (
    MIN_VALID_SAMPLES_FOR_SLOPE,
    compute_segment_terrain,
    _resample_line,
)


class FakeDem:
    """A DEM test double: elevation_at is any function of (lat, lon)."""

    def __init__(self, fn):
        self.fn = fn

    def elevation_at(self, lat, lon):
        return self.fn(lat, lon)


# A short straight "segment" running north for ~500m at ~26N, 91E.
FLAT_LINE = [(91.0, 26.0), (91.0, 26.0045)]


def test_valid_geometry_yields_valid_elevation():
    dem = FakeDem(lambda lat, lon: 100.0)
    terrain = compute_segment_terrain(FLAT_LINE, dem)
    assert terrain.elevation_m == 100.0
    assert terrain.valid_sample_count > 0
    assert terrain.sample_count >= terrain.valid_sample_count


def test_flat_profile_gives_zero_slope():
    dem = FakeDem(lambda lat, lon: 250.0)
    terrain = compute_segment_terrain(FLAT_LINE, dem)
    assert terrain.slope_deg == 0.0
    assert terrain.slope_percent == 0.0


def test_monotonic_uphill_gives_positive_slope_matching_manual_calc():
    # Elevation increases linearly with latitude: 100000 m per degree of
    # latitude (an arbitrary steep-but-simple gradient chosen purely to make
    # the expected value easy to hand-compute).
    METERS_PER_DEGREE_LAT = 100000.0
    dem = FakeDem(lambda lat, lon: (lat - 26.0) * METERS_PER_DEGREE_LAT)

    line = [(91.0, 26.0), (91.0, 26.01)]  # ~1.1km north
    terrain = compute_segment_terrain(line, dem)

    assert terrain.slope_deg is not None
    assert terrain.slope_deg > 0

    # Manually compute the expected gradient the same way dem_processor
    # does: total |elevation change| / total horizontal distance.
    from app.core.geo import haversine_km

    horizontal_m = haversine_km(26.0, 91.0, 26.01, 91.0) * 1000.0
    vertical_m = (26.01 - 26.0) * METERS_PER_DEGREE_LAT
    expected_deg = math.degrees(math.atan(vertical_m / horizontal_m))
    assert terrain.slope_deg == pytest.approx(expected_deg, rel=1e-3)


def test_climb_then_descend_is_not_reported_as_flat():
    """A road that climbs then descends back to roughly its starting
    elevation must NOT report ~0 slope just because start/end elevations
    match — that's exactly the failure mode Part 4.8 requires avoiding."""
    # Tent-shaped profile: elevation rises from 0 to 500m at the midpoint,
    # then falls back to 0 by the end. Endpoints have equal elevation, so a
    # naive (end - start) / distance calculation would give exactly 0.
    lat_start, lat_mid, lat_end = 26.0, 26.01, 26.02
    peak_elevation = 500.0

    def tent(lat, lon):
        if lat <= lat_mid:
            frac = (lat - lat_start) / (lat_mid - lat_start)
        else:
            frac = (lat_end - lat) / (lat_end - lat_mid)
        return peak_elevation * frac

    dem = FakeDem(tent)
    line = [(91.0, lat_start), (91.0, lat_mid), (91.0, lat_end)]
    terrain = compute_segment_terrain(line, dem)

    # Naive endpoint method would give ~0 (start and end are both ~0m).
    naive_endpoint_change = abs(tent(lat_end, 91.0) - tent(lat_start, 91.0))
    assert naive_endpoint_change < 1.0  # confirms the naive method WOULD be misleading here

    # The real (abs-gradient) method must report a clearly non-flat slope.
    assert terrain.slope_deg is not None
    assert terrain.slope_deg > 5.0  # a 500m climb over ~1.1km is a steep, unmistakably non-flat grade


def test_missing_dem_returns_none_not_fabricated_value():
    dem = FakeDem(lambda lat, lon: None)
    terrain = compute_segment_terrain(FLAT_LINE, dem)
    assert terrain.elevation_m is None
    assert terrain.slope_deg is None
    assert terrain.valid_sample_count == 0
    assert terrain.sample_count > 0  # points were generated, just none had DEM coverage


def test_partial_dem_coverage_uses_only_valid_points():
    # Points north of 26.002 are "outside DEM coverage" for this fake tile.
    def partial(lat, lon):
        if lat > 26.002:
            return None
        return 42.0

    dem = FakeDem(partial)
    terrain = compute_segment_terrain(FLAT_LINE, dem)
    assert terrain.elevation_m == 42.0
    assert 0 < terrain.valid_sample_count < terrain.sample_count


def test_single_valid_sample_cannot_derive_slope():
    calls = {"n": 0}

    def only_first_valid(lat, lon):
        calls["n"] += 1
        return 10.0 if calls["n"] == 1 else None

    dem = FakeDem(only_first_valid)
    terrain = compute_segment_terrain(FLAT_LINE, dem)
    assert terrain.valid_sample_count < MIN_VALID_SAMPLES_FOR_SLOPE
    assert terrain.slope_deg is None
    assert terrain.elevation_m == 10.0  # representative elevation still computable from 1 point


def test_empty_geometry_does_not_crash():
    dem = FakeDem(lambda lat, lon: 100.0)
    terrain = compute_segment_terrain([], dem)
    assert terrain.elevation_m is None
    assert terrain.slope_deg is None
    assert terrain.sample_count == 0


def test_single_point_geometry_does_not_crash():
    dem = FakeDem(lambda lat, lon: 100.0)
    terrain = compute_segment_terrain([(91.0, 26.0)], dem)
    assert terrain.sample_count == 1
    assert terrain.elevation_m == 100.0
    assert terrain.slope_deg is None  # only one point, no gradient to compute


def test_zero_length_edge_is_skipped_without_crashing():
    dem = FakeDem(lambda lat, lon: 50.0)
    line = [(91.0, 26.0), (91.0, 26.0), (91.0, 26.005)]  # duplicate point in the middle
    terrain = compute_segment_terrain(line, dem)
    assert terrain.elevation_m == 50.0


def test_resample_line_includes_original_endpoints():
    line = [(91.0, 26.0), (91.02, 26.03)]
    points = _resample_line(line, interval_m=90.0)
    assert points[0] == (26.0, 91.0)
    assert points[-1] == (26.03, 91.02)
    assert len(points) > 2  # a several-km edge must produce intermediate samples too
