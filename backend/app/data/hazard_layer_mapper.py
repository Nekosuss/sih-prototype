"""
Part 11: pre-computes per-segment landslide/flood hazard-zonation results
for every real road segment in the current network, writing
derived/road_hazard_layer_features.csv -- mirrors landslide_mapper.py's
role for the GSI historical inventory (Part 4), and dem_validation.py's
role for DEM terrain features (Part 4.8).

--- Usage ---
    cd backend
    python -m app.data.hazard_layer_mapper

--- Honest current result ---

Whether this produces any REAL coverage at all depends entirely on whether
an official APSAC hazard-zonation layer has actually been dropped into
app/data/hazard_layers/ (see app/data/hazard_layer_loader.py's module
docstring for the verified access status). At Part 11 implementation time,
no such file exists, so running this script writes a CSV where every
segment's status is `no_coverage` and every class/score is empty --
honestly reflecting that no official production layer is locally
available, never fabricating a value. See main() below: this script
deliberately does NOT get run against the real network as part of Part 11
delivery, and its all-empty output is NOT committed to the repository --
committing 2,964 rows of nothing would add no information and could read
as if a real dataset were behind it. network_loader.py's enrichment step
(_maybe_enrich_with_hazard_layer_features) simply skips merging when this
CSV doesn't exist, so RoadSegment.landslide_hazard_score/flood_hazard_score
stay at their honest default (None) until a real layer is used to
regenerate this file -- the exact same missing-derived-file fallback
landslide_mapper.py's CSV already relies on.
"""
from pathlib import Path

import pandas as pd

from app.core.hazard_layer_service import segment_flood_hazard, segment_landslide_hazard
from app.data.hazard_layer_loader import HazardLayerLoader, get_default_hazard_layer_loader
from app.data.network_loader import load_network
from app.models.network import RoadSegment

DERIVED_DIR = Path(__file__).parent / "derived"
DEFAULT_FEATURES_CSV = DERIVED_DIR / "road_hazard_layer_features.csv"


def build_features(segments: list[RoadSegment], loader: HazardLayerLoader = None) -> pd.DataFrame:
    loader = loader or get_default_hazard_layer_loader()
    rows = []
    for segment in segments:
        landslide = segment_landslide_hazard(segment, loader=loader)
        flood = segment_flood_hazard(segment, loader=loader)
        rows.append({
            "segment_id": segment.id,
            "landslide_hazard_class": landslide.hazard_class,
            "landslide_hazard_score": landslide.hazard_score,
            "landslide_hazard_status": landslide.status.value,
            "flood_hazard_class": flood.hazard_class,
            "flood_hazard_score": flood.hazard_score,
            "flood_hazard_status": flood.status.value,
        })
    return pd.DataFrame(rows)


def write_features_csv(df: pd.DataFrame, path: Path = DEFAULT_FEATURES_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def enrich_segments_with_hazard_layer_features(segments: list[RoadSegment], features_df: pd.DataFrame) -> list[RoadSegment]:
    """Returns NEW RoadSegment objects with landslide_hazard_*/flood_hazard_*
    populated from features_df -- mirrors
    landslide_mapper.enrich_segments_with_landslide_features(). Never
    mutates the input segments, and never touches
    historical_landslide_count/nearest_landslide_distance_m/slope_deg/
    elevation_m/geometry -- those remain exactly as the DEM/GSI/OSM
    pipelines set them."""
    features_by_id = features_df.set_index("segment_id")
    enriched = []
    for segment in segments:
        row = features_by_id.loc[segment.id]
        update: dict = {}
        hazard_layer_source = dict(segment.hazard_layer_source)

        if pd.notna(row["landslide_hazard_score"]):
            update["landslide_hazard_class"] = row["landslide_hazard_class"]
            update["landslide_hazard_score"] = float(row["landslide_hazard_score"])
            hazard_layer_source["landslide_hazard"] = "real: APSAC landslide hazard zonation"

        if pd.notna(row["flood_hazard_score"]):
            update["flood_hazard_class"] = row["flood_hazard_class"]
            update["flood_hazard_score"] = float(row["flood_hazard_score"])
            hazard_layer_source["flood_hazard"] = "real: APSAC flood hazard zonation"

        if update:
            update["hazard_layer_source"] = hazard_layer_source
            enriched.append(segment.model_copy(update=update))
        else:
            enriched.append(segment)
    return enriched


def main():
    nodes, segments = load_network()
    df = build_features(segments)
    write_features_csv(df)

    covered_landslide = int((df["landslide_hazard_status"] == "ok").sum())
    covered_flood = int((df["flood_hazard_status"] == "ok").sum())
    print(f"Wrote {DEFAULT_FEATURES_CSV} ({len(df)} rows)")
    print(f"Segments with real landslide hazard-zonation coverage: {covered_landslide} / {len(df)}")
    print(f"Segments with real flood hazard-zonation coverage: {covered_flood} / {len(df)}")
    if covered_landslide == 0 and covered_flood == 0:
        print()
        print("No official APSAC hazard-zonation layer is locally available -- see")
        print("app/data/hazard_layer_loader.py's module docstring for the verified access status.")
        print("This CSV honestly reflects zero real coverage and is NOT committed to the repository")
        print("for exactly that reason (see backend/app/data/README.md, Part 11).")


if __name__ == "__main__":
    main()
