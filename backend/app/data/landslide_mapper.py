"""
Spatial join: GSI landslide inventory -> real OSM road segments.

This is a DATA ENGINEERING pipeline, not a risk model. It turns raw GSI
field observations (backend/app/data/gsi_landslides_corridor.csv) into two
traceable derived datasets under backend/app/data/derived/:

  1. gsi_landslides_corridor_mapped.csv (+ .geojson) — every landslide
     record, unchanged, plus its nearest OSM road segment (if within
     MATCH_THRESHOLD_M) and the true nearest-road distance.
  2. road_landslide_features.csv — for every OSM road segment in the
     current network, how many historical landslides matched to it and how
     far the nearest one is.

No probability, no risk score, no label, no ML — just a real,
distance-thresholded nearest-neighbor spatial join, done in a projected
(metric) CRS so "meters" actually means meters. See README.md in this
folder for what downstream steps (an ML training dataset) still need.

--- Coordinate handling ---
The OSM GeoJSON is CRS84 (WGS84, [lon, lat] axis order) — the same
reference system as EPSG:4326. The GSI CSV's `latitude`/`longitude` columns
are plain WGS84 decimal degrees. shapely.geometry.Point takes (x, y), i.e.
(longitude, latitude) — get this backwards and every point silently lands
in the wrong place (often the wrong side of the globe), which is why
tests/test_landslide_mapper.py checks it explicitly. Both datasets are
reprojected to a UTM zone estimated from the road network's own extent
before any distance is computed — EPSG:4326 coordinates are degrees, not
meters, so a raw distance in that CRS is not a metric distance.

--- Usage ---
    cd backend
    python -m app.data.landslide_mapper
"""
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from app.data.network_loader import load_network
from app.models.network import RoadSegment

DATA_DIR = Path(__file__).parent
DERIVED_DIR = DATA_DIR / "derived"

DEFAULT_GSI_CSV = DATA_DIR / "gsi_landslides_corridor.csv"
DEFAULT_MAPPED_CSV = DERIVED_DIR / "gsi_landslides_corridor_mapped.csv"
DEFAULT_MAPPED_GEOJSON = DERIVED_DIR / "gsi_landslides_corridor_mapped.geojson"
DEFAULT_FEATURES_CSV = DERIVED_DIR / "road_landslide_features.csv"

DEFAULT_MATCH_THRESHOLD_M = 500.0

REQUIRED_GSI_COLUMNS = [
    "slide_no", "slide_id", "state",
    "description_before_coordinates", "latitude", "longitude",
    "description_after_coordinates", "raw_record",
]


# ---------------------------------------------------------------------------
# Step 1-ish: load + validate the raw GSI CSV
# ---------------------------------------------------------------------------

def load_gsi_csv(path: Path = DEFAULT_GSI_CSV) -> pd.DataFrame:
    """
    Loads the raw GSI CSV as-is (source records are never modified — see
    module docstring). Validates that latitude/longitude are present and
    numeric and fall within valid ranges; raises rather than silently
    dropping a bad row, since a malformed coordinate is a real data problem
    worth surfacing, not hiding.
    """
    df = pd.read_csv(path, encoding="utf-8-sig")

    missing_cols = set(REQUIRED_GSI_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"GSI CSV {path} is missing expected columns: {sorted(missing_cols)}")

    df["latitude"] = pd.to_numeric(df["latitude"], errors="raise")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="raise")
    if df["latitude"].isna().any() or df["longitude"].isna().any():
        raise ValueError(f"GSI CSV {path} has missing latitude/longitude values")
    if not df["latitude"].between(-90, 90).all():
        raise ValueError(f"GSI CSV {path} has latitude values outside [-90, 90]")
    if not df["longitude"].between(-180, 180).all():
        raise ValueError(f"GSI CSV {path} has longitude values outside [-180, 180]")

    return df


# ---------------------------------------------------------------------------
# Step 2: the spatial join itself
# ---------------------------------------------------------------------------

def _segment_linestring(segment: RoadSegment) -> LineString:
    return LineString([(p.lng, p.lat) for p in segment.geometry])


def segments_to_geodataframe(segments: list[RoadSegment]) -> gpd.GeoDataFrame:
    """Road segment geometry, in WGS84 (EPSG:4326) — the same CRS the OSM
    GeoJSON itself declares (CRS84)."""
    return gpd.GeoDataFrame(
        {
            "segment_id": [s.id for s in segments],
            "road_name": [s.name for s in segments],
            "highway_class": [s.road_type.value for s in segments],
        },
        geometry=[_segment_linestring(s) for s in segments],
        crs="EPSG:4326",
    )


def landslides_to_geodataframe(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """longitude = x, latitude = y — shapely.Point(x, y), i.e. Point(lon, lat)."""
    geometry = [Point(lon, lat) for lon, lat in zip(df["longitude"], df["latitude"])]
    return gpd.GeoDataFrame(df.copy(), geometry=geometry, crs="EPSG:4326")


def spatial_join_landslides_to_segments(
    landslides_df: pd.DataFrame,
    segments: list[RoadSegment],
    threshold_m: float = DEFAULT_MATCH_THRESHOLD_M,
) -> pd.DataFrame:
    """
    Returns a copy of landslides_df with these columns added:
      distance_to_road_m   — true nearest-road distance, in meters, ALWAYS
                              populated (even past the threshold — kept for
                              diagnostics, e.g. "this one was 3km away").
      matched_segment_id, matched_road_name, matched_highway_class
                            — populated only when within threshold_m.
      match_status          — "MATCHED" or "UNMATCHED".

    Every input record is kept (a record further than threshold_m from any
    road is UNMATCHED, not dropped).
    """
    if not segments:
        raise ValueError("No road segments to join against")

    landslides_gdf = landslides_to_geodataframe(landslides_df)
    segments_gdf = segments_to_geodataframe(segments)

    # Never compute meter distances directly in EPSG:4326 (degrees) — project
    # both to a metric CRS. estimate_utm_crs() picks the UTM zone matching
    # the road network's own extent, rather than a hardcoded zone number.
    projected_crs = segments_gdf.estimate_utm_crs()
    landslides_proj = landslides_gdf.to_crs(projected_crs)
    segments_proj = segments_gdf.to_crs(projected_crs)

    joined = gpd.sjoin_nearest(
        landslides_proj,
        segments_proj[["segment_id", "road_name", "highway_class", "geometry"]],
        how="left",
        distance_col="distance_to_road_m",
    )
    # sjoin_nearest returns one row per exact-tie nearest match; on the rare
    # exact tie, keep just one (closest, or first if equal) per input point.
    joined = joined.sort_values("distance_to_road_m").groupby(level=0).first()
    joined = joined.reindex(landslides_gdf.index)

    result = landslides_df.reset_index(drop=True).copy()
    result["distance_to_road_m"] = joined["distance_to_road_m"].to_numpy().round(2)
    is_matched = result["distance_to_road_m"] <= threshold_m

    result["matched_segment_id"] = joined["segment_id"].to_numpy()
    result["matched_road_name"] = joined["road_name"].to_numpy()
    result["matched_highway_class"] = joined["highway_class"].to_numpy()
    result.loc[~is_matched, ["matched_segment_id", "matched_road_name", "matched_highway_class"]] = None
    result["match_status"] = is_matched.map({True: "MATCHED", False: "UNMATCHED"})

    return result


# ---------------------------------------------------------------------------
# Step 4: aggregate matched landslides onto every road segment
# ---------------------------------------------------------------------------

def aggregate_to_road_segments(mapped_df: pd.DataFrame, segments: list[RoadSegment]) -> pd.DataFrame:
    """
    One row per road segment currently in the network (not just segments
    with a match): historical_landslide_count (0 if none — matched records
    are kept individually, never deduplicated, so a segment with 3 separate
    GSI observations counts 3) and nearest_landslide_distance_m (null when
    count is 0).
    """
    matched = mapped_df[mapped_df["match_status"] == "MATCHED"]
    counts = matched.groupby("matched_segment_id").size()
    nearest = matched.groupby("matched_segment_id")["distance_to_road_m"].min()

    rows = []
    for segment in segments:
        count = int(counts.get(segment.id, 0))
        rows.append({
            "segment_id": segment.id,
            "historical_landslide_count": count,
            "nearest_landslide_distance_m": round(float(nearest[segment.id]), 2) if count > 0 else None,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 5 (optional helper, not wired into network_loader — see README):
# apply computed features onto RoadSegment objects in memory.
# ---------------------------------------------------------------------------

def enrich_segments_with_landslide_features(
    segments: list[RoadSegment],
    features_df: pd.DataFrame,
) -> list[RoadSegment]:
    """Returns NEW RoadSegment objects with historical_landslide_count /
    nearest_landslide_distance_m populated from features_df. Does not
    mutate the input segments, and does not touch base_risk/
    current_risk_score/routing behavior — those fields are informational
    only until a later step decides how (or whether) to use them."""
    features_by_id = features_df.set_index("segment_id")
    enriched = []
    for segment in segments:
        row = features_by_id.loc[segment.id]
        enriched.append(
            segment.model_copy(
                update={
                    "historical_landslide_count": int(row["historical_landslide_count"]),
                    "nearest_landslide_distance_m": (
                        None if pd.isna(row["nearest_landslide_distance_m"])
                        else float(row["nearest_landslide_distance_m"])
                    ),
                }
            )
        )
    return enriched


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_mapped_csv(mapped_df: pd.DataFrame, path: Path = DEFAULT_MAPPED_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mapped_df.to_csv(path, index=False)


def write_mapped_geojson(mapped_df: pd.DataFrame, path: Path = DEFAULT_MAPPED_GEOJSON) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = landslides_to_geodataframe(mapped_df)
    gdf.to_file(path, driver="GeoJSON")


def write_features_csv(features_df: pd.DataFrame, path: Path = DEFAULT_FEATURES_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    features_df.to_csv(path, index=False)


# ---------------------------------------------------------------------------
# Step 7: validation summary
# ---------------------------------------------------------------------------

def print_validation_summary(mapped_df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    total = len(mapped_df)
    matched = int((mapped_df["match_status"] == "MATCHED").sum())
    unmatched = total - matched
    match_rate = (matched / total * 100) if total else 0.0

    print(f"Total GSI corridor landslides: {total}")
    print(f"Matched to OSM roads: {matched}")
    print(f"Unmatched: {unmatched}")
    print(f"Match rate: {match_rate:.1f}%")
    print()

    segs_with_landslides = int((features_df["historical_landslide_count"] >= 1).sum())
    max_count = int(features_df["historical_landslide_count"].max()) if len(features_df) else 0
    print(f"Road segments with >=1 historical landslide: {segs_with_landslides}")
    print(f"Maximum landslides on one segment: {max_count}")
    print()

    print("10 closest matched examples:")
    closest = mapped_df[mapped_df["match_status"] == "MATCHED"].nsmallest(10, "distance_to_road_m")
    for _, row in closest.iterrows():
        print(
            f"  slide_no={row['slide_no']} slide_id={row['slide_id']} "
            f"-> segment={row['matched_segment_id']} ({row['matched_road_name']}) "
            f"distance_m={row['distance_to_road_m']} "
            f"lat={row['latitude']} lng={row['longitude']}"
        )


def run_pipeline(
    gsi_csv_path: Path = DEFAULT_GSI_CSV,
    threshold_m: float = DEFAULT_MATCH_THRESHOLD_M,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes, segments = load_network()
    landslides_df = load_gsi_csv(gsi_csv_path)
    mapped_df = spatial_join_landslides_to_segments(landslides_df, segments, threshold_m)
    features_df = aggregate_to_road_segments(mapped_df, segments)
    return mapped_df, features_df


if __name__ == "__main__":
    mapped_df, features_df = run_pipeline()
    write_mapped_csv(mapped_df)
    write_mapped_geojson(mapped_df)
    write_features_csv(features_df)
    print_validation_summary(mapped_df, features_df)
