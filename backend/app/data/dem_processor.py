"""
Derives per-road-segment terrain features (representative elevation, slope)
from a DEM (see dem_loader.py). Pure geometry-in / numbers-out module: it
knows nothing about RoadSegment, OSM tags, or routing — osm_geojson_loader.py
is the only caller, and it decides what to do with the result (including
falling back and labelling the fallback honestly if the DEM has no usable
data for a given segment). This split is what lets the DEM source be
swapped later without touching this file's slope/elevation math, and lets
this file's math be unit-tested with a fake DEM instead of the real
~50MB tile set (see tests/test_dem_processor.py).

--- Representative elevation ---

A road segment is a polyline, not a point. Sampling only one endpoint (or
the midpoint, as the old nearest-reference-town approximation effectively
did) throws away real elevation change along mountain hairpins. Instead:

  1. The segment's geometry is resampled at ~SAMPLE_INTERVAL_M spacing
     (see _resample_line) so a several-hundred-metre mountain segment gets
     multiple real DEM samples, not just its two endpoints.
  2. Each resampled point is queried against the DEM.
  3. Representative elevation_m = the mean of all successfully sampled
     elevations along the segment.

--- Slope ---

Naively computing slope from (end elevation - start elevation) / distance
fails on a segment that climbs and then descends (or vice versa) within
itself — a hairpin that gains 40m then loses 40m would report ~0 deg slope,
which is wrong for hazard/terrain purposes: the road surface still climbed
and fell by real, non-trivial amounts.

Instead this module computes slope from the full sampled profile as the
**mean absolute gradient magnitude**:

    slope_percent = (sum of |elevation change| between consecutive valid
                      sample points) / (total horizontal distance between
                      those points) * 100
    slope_deg = degrees(atan(slope_percent / 100))

Summing the *absolute* value of each consecutive change (rather than the
single net start-to-end change) means climbs and descents both contribute
positively — a segment that goes up then down by similar amounts reports a
slope reflecting that undulation, not a false "flat" reading. This is a
terrain-roughness / average-gradient metric, not the net grade between the
two endpoints; that distinction is documented wherever slope_deg is
reported (RoadSegment.source, README.md).

Units: slope_deg is in degrees, always >= 0 (magnitude, not direction —
a road doesn't have a single "direction" of slope along an undulating
profile). elevation_m is in metres (EGM96 geoid height, matching the DEM's
native vertical datum — see dem_loader.py).
"""
import math
from dataclasses import dataclass
from typing import Optional

from app.core.geo import haversine_km

SAMPLE_INTERVAL_M = 90.0  # ~3x the DEM's native ~30m pixel spacing
MIN_VALID_SAMPLES_FOR_SLOPE = 2


@dataclass
class SegmentTerrain:
    elevation_m: Optional[float]
    slope_deg: Optional[float]
    slope_percent: Optional[float]
    sample_count: int
    valid_sample_count: int
    min_elevation_m: Optional[float]
    max_elevation_m: Optional[float]


def _resample_line(coords_lonlat: list, interval_m: float) -> list:
    """Returns [(lat, lon), ...] along the polyline, always including every
    original vertex (so real OSM intersection points are never skipped)
    plus extra interpolated points wherever an original edge is longer than
    interval_m. Interpolation is linear in lat/lon between the two real
    endpoints of each edge — over distances this short (segments here run
    metres to a few km) that's indistinguishable from the true geodesic and
    keeps this module dependency-free (no UTM reprojection needed just to
    place sample points)."""
    if not coords_lonlat:
        return []

    points = [(coords_lonlat[0][1], coords_lonlat[0][0])]
    for i in range(len(coords_lonlat) - 1):
        lon1, lat1 = coords_lonlat[i]
        lon2, lat2 = coords_lonlat[i + 1]
        edge_len_m = haversine_km(lat1, lon1, lat2, lon2) * 1000.0
        if edge_len_m <= 0:
            continue
        n_sub = max(1, math.ceil(edge_len_m / interval_m))
        for k in range(1, n_sub + 1):
            f = k / n_sub
            points.append((lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f))
    return points


def _cumulative_distances_m(points_latlon: list) -> list:
    dists = [0.0]
    for i in range(1, len(points_latlon)):
        lat1, lon1 = points_latlon[i - 1]
        lat2, lon2 = points_latlon[i]
        dists.append(dists[-1] + haversine_km(lat1, lon1, lat2, lon2) * 1000.0)
    return dists


def compute_segment_terrain(coords_lonlat: list, dem, interval_m: float = SAMPLE_INTERVAL_M) -> SegmentTerrain:
    """coords_lonlat: the segment geometry as [(lon, lat), ...] — the same
    [lon, lat] order OSM GeoJSON uses. dem: anything with an
    elevation_at(lat, lon) -> Optional[float] method (see dem_loader.DemLoader;
    tests may pass a fake)."""
    points = _resample_line(coords_lonlat, interval_m)
    distances = _cumulative_distances_m(points)

    elevations = [dem.elevation_at(lat, lon) for lat, lon in points]
    valid = [(d, e) for d, e in zip(distances, elevations) if e is not None]

    if not valid:
        return SegmentTerrain(
            elevation_m=None,
            slope_deg=None,
            slope_percent=None,
            sample_count=len(points),
            valid_sample_count=0,
            min_elevation_m=None,
            max_elevation_m=None,
        )

    valid_elevations = [e for _, e in valid]
    elevation_m = sum(valid_elevations) / len(valid_elevations)
    min_elevation_m = min(valid_elevations)
    max_elevation_m = max(valid_elevations)

    slope_deg: Optional[float] = None
    slope_percent: Optional[float] = None
    if len(valid) >= MIN_VALID_SAMPLES_FOR_SLOPE:
        total_abs_change = 0.0
        total_horizontal_m = 0.0
        prev_dist, prev_elev = valid[0]
        for dist, elev in valid[1:]:
            total_abs_change += abs(elev - prev_elev)
            total_horizontal_m += dist - prev_dist
            prev_dist, prev_elev = dist, elev
        if total_horizontal_m > 0:
            slope_percent = (total_abs_change / total_horizontal_m) * 100.0
            slope_deg = math.degrees(math.atan(total_abs_change / total_horizontal_m))

    return SegmentTerrain(
        elevation_m=elevation_m,
        slope_deg=slope_deg,
        slope_percent=slope_percent,
        sample_count=len(points),
        valid_sample_count=len(valid),
        min_elevation_m=min_elevation_m,
        max_elevation_m=max_elevation_m,
    )
