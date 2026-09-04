"""
Validates the real-DEM-derived elevation_m/slope_deg now populated on every
RoadSegment (Part 4.8 — see osm_geojson_loader.py, dem_loader.py,
dem_processor.py) and reports on data quality. This is a READ-ONLY
reporting pass — it does not change routing, risk scoring, or the DEM
sampling logic itself, and it does not train anything.

--- Usage ---
    cd backend
    python -m app.data.dem_validation

Also writes `derived/road_segment_terrain.csv` — one row per road segment
with its elevation_m/slope_deg/terrain_type and DEM sample-quality counts,
for reuse in later parts (e.g. the ML training dataset design in
training_dataset_schema.md) without needing to reload the whole network.

No expected elevation/slope values are hard-coded anywhere in this module —
every number below is read directly off the live RoadSegment objects
produced by load_network(), which itself samples the real cached SRTM1
tiles (see README.md "DEM provenance").
"""
from pathlib import Path
from statistics import median

import pandas as pd

from app.core.geo import haversine_km
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.network_loader import load_network

DERIVED_DIR = Path(__file__).parent / "derived"
DEFAULT_OUTPUT_CSV = DERIVED_DIR / "road_segment_terrain.csv"

# Broad plausibility bounds for this specific corridor (Assam plains to the
# Eastern Himalaya near Tawang/Sela) — NOT a hard validity rule, just a
# threshold for flagging values worth a human look. The corridor's real
# terrain runs from ~45m (Brahmaputra valley) to a bit above the ~4200m
# Sela Pass summit; a wide margin is kept on both sides.
PLAUSIBLE_ELEVATION_RANGE_M = (-20.0, 6000.0)
# A sustained road grade above ~25 deg (~47%) is extremely rare in the real
# world (hairpins aside); flag for review rather than silently accept or reject.
SUSPICIOUS_SLOPE_DEG = 25.0


def _nearest_segment(segments, lat, lng):
    def mid(s):
        return s.geometry[len(s.geometry) // 2]

    return min(segments, key=lambda s: haversine_km(lat, lng, mid(s).lat, mid(s).lng))


def main():
    nodes, segments = load_network()
    total = len(segments)

    with_elevation = [s for s in segments if s.elevation_m is not None]
    with_slope = [s for s in segments if s.slope_deg is not None]
    missing_elevation = total - len(with_elevation)
    missing_slope = total - len(with_slope)

    fallback_elevation = [s for s in segments if "fallback" in s.source.get("elevation_m", "")]
    real_dem_elevation = [s for s in segments if s.source.get("elevation_m", "").startswith("real:")]

    elevations = [s.elevation_m for s in with_elevation]
    slopes = [s.slope_deg for s in with_slope]

    suspicious_elevation = [
        s for s in with_elevation
        if not (PLAUSIBLE_ELEVATION_RANGE_M[0] <= s.elevation_m <= PLAUSIBLE_ELEVATION_RANGE_M[1])
    ]
    suspicious_slope = [s for s in with_slope if s.slope_deg > SUSPICIOUS_SLOPE_DEG]

    print("=== DEM sampling coverage ===")
    print(f"Total road segments: {total}")
    print(f"Elevation successfully sampled: {len(with_elevation)} ({100 * len(with_elevation) / total:.1f}%)")
    print(f"  ...from real DEM samples: {len(real_dem_elevation)}")
    print(f"  ...from fallback (nearest-reference-town) approximation: {len(fallback_elevation)}")
    print(f"Missing elevation: {missing_elevation}")
    print(f"Slope successfully derived: {len(with_slope)} ({100 * len(with_slope) / total:.1f}%)")
    print(f"Missing slope: {missing_slope}")
    print()

    print("=== Elevation statistics (metres) ===")
    if elevations:
        print(f"Minimum: {min(elevations):.1f}")
        print(f"Median:  {median(elevations):.1f}")
        print(f"Maximum: {max(elevations):.1f}")
    else:
        print("No elevation samples available.")
    print()

    print("=== Slope statistics (degrees) ===")
    if slopes:
        print(f"Minimum: {min(slopes):.2f}")
        print(f"Median:  {median(slopes):.2f}")
        print(f"Maximum: {max(slopes):.2f}")
    else:
        print("No slope samples available.")
    print()

    print(f"=== Suspicious/extreme values (outside plausibility thresholds, not hard rules) ===")
    print(f"Elevation outside {PLAUSIBLE_ELEVATION_RANGE_M}m: {len(suspicious_elevation)}")
    for s in suspicious_elevation[:10]:
        print(f"  {s.id}: elevation_m={s.elevation_m}")
    print(f"Slope above {SUSPICIOUS_SLOPE_DEG} deg: {len(suspicious_slope)}")
    for s in suspicious_slope[:10]:
        print(f"  {s.id}: slope_deg={s.slope_deg}, elevation_m={s.elevation_m}")
    print()

    print("=== Sample values near each named corridor location ===")
    print("(nearest road segment by geometry midpoint; not the town's own coordinate)")
    for loc in DEMO_LOCATIONS:
        seg = _nearest_segment(segments, loc["lat"], loc["lng"])
        print(
            f"{loc['name']:12s} -> {seg.id}: elevation_m={seg.elevation_m}, "
            f"slope_deg={seg.slope_deg}, terrain_type={seg.terrain_type.value}, "
            f"source={seg.source.get('elevation_m')}"
        )
    print()

    print("=== Elevation trend check (informational - not asserted) ===")
    print("Mean real-DEM elevation of the 5 nearest segments to each location, in corridor order:")
    for loc in DEMO_LOCATIONS:
        nearest5 = sorted(
            segments,
            key=lambda s: haversine_km(loc["lat"], loc["lng"], s.geometry[len(s.geometry) // 2].lat, s.geometry[len(s.geometry) // 2].lng),
        )[:5]
        vals = [s.elevation_m for s in nearest5 if s.elevation_m is not None]
        mean_elev = sum(vals) / len(vals) if vals else float("nan")
        print(f"  {loc['name']:12s}: {mean_elev:8.1f} m  (n={len(vals)})")

    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "segment_id": s.id,
                "elevation_m": s.elevation_m,
                "slope_deg": s.slope_deg,
                "terrain_type": s.terrain_type.value,
                "elevation_source": s.source.get("elevation_m"),
                "slope_source": s.source.get("slope_deg"),
            }
            for s in segments
        ]
    )
    df.to_csv(DEFAULT_OUTPUT_CSV, index=False)
    print()
    print(f"Wrote {DEFAULT_OUTPUT_CSV} ({len(df)} rows)")


if __name__ == "__main__":
    main()
