"""
Integration tests against the REAL corridor network loaded with the REAL
cached DEM tiles (see conftest.py's `network` fixture, which calls
load_network() with its default use_dem=True). These check that the real
pipeline actually produced real, sane, differentiated terrain data — not
that specific numbers match a hard-coded expectation (Part 4.8 explicitly
forbids hard-coding expected elevations to make a test pass).
"""
from app.core.geo import haversine_km
from app.data.demo_locations import DEMO_LOCATIONS


def _nearest_segments(segments, lat, lng, n=5):
    def mid(s):
        return s.geometry[len(s.geometry) // 2]

    return sorted(segments, key=lambda s: haversine_km(lat, lng, mid(s).lat, mid(s).lng))[:n]


def test_almost_all_segments_get_a_real_elevation_sample(network):
    _, segments = network
    with_elevation = [s for s in segments if s.elevation_m is not None]
    # The corridor's full bounding box is covered by the cached tiles, so
    # coverage should be effectively complete; a small allowance is kept
    # only for genuine DEM voids, not for a broken pipeline.
    assert len(with_elevation) / len(segments) >= 0.99


def test_most_sampled_segments_used_the_real_dem_not_the_fallback(network):
    _, segments = network
    real_dem = [s for s in segments if s.source.get("elevation_m", "").startswith("real:")]
    assert len(real_dem) / len(segments) >= 0.99


def test_slope_is_populated_for_almost_every_segment(network):
    _, segments = network
    with_slope = [s for s in segments if s.slope_deg is not None]
    assert len(with_slope) / len(segments) >= 0.99


def test_slope_values_are_non_negative_and_bounded(network):
    _, segments = network
    for s in segments:
        if s.slope_deg is not None:
            assert 0.0 <= s.slope_deg < 90.0


def test_mountain_towns_show_meaningfully_higher_elevation_than_plains_towns(network):
    """Guwahati/Tezpur (Brahmaputra plains) vs. Bomdila/Tawang (Eastern
    Himalaya) must be clearly differentiated by the real DEM — this is the
    exact thing the old 7-point nearest-town step function could only
    fake at 7 exact spots; here it must emerge from real per-segment
    sampling across many nearby segments."""
    _, segments = network

    def mean_elevation_near(name):
        loc = next(l for l in DEMO_LOCATIONS if l["name"] == name)
        nearby = _nearest_segments(segments, loc["lat"], loc["lng"])
        vals = [s.elevation_m for s in nearby if s.elevation_m is not None]
        assert vals, f"no sampled elevation found near {name}"
        return sum(vals) / len(vals)

    plains_elevation = mean_elevation_near("Guwahati")
    mountain_elevation = mean_elevation_near("Bomdila")

    # No specific expected values asserted — only that real terrain
    # produces a large, obvious difference between a river-valley town and
    # a Himalayan hill-station town.
    assert mountain_elevation - plains_elevation > 1000.0


def test_mountain_segments_have_higher_typical_slope_than_plains_segments(network):
    _, segments = network

    def median_slope_near(name):
        loc = next(l for l in DEMO_LOCATIONS if l["name"] == name)
        nearby = _nearest_segments(segments, loc["lat"], loc["lng"], n=15)
        vals = sorted(s.slope_deg for s in nearby if s.slope_deg is not None)
        assert vals, f"no slope found near {name}"
        return vals[len(vals) // 2]

    plains_slope = median_slope_near("Guwahati")
    mountain_slope = median_slope_near("Sela Pass")

    assert mountain_slope > plains_slope


def test_terrain_type_still_agrees_with_the_now_real_elevation(network):
    """terrain_type is a threshold classification of elevation_m
    (classify_terrain in osm_geojson_loader.py) — this must stay
    consistent even though elevation_m now comes from a real DEM sample
    instead of the old approximation."""
    from app.data.osm_geojson_loader import classify_terrain

    _, segments = network
    for s in segments:
        if s.elevation_m is not None:
            assert s.terrain_type == classify_terrain(s.elevation_m)
