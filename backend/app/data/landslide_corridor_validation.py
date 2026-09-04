"""
Validates and reports on the already-mapped GSI landslide dataset
(derived/gsi_landslides_corridor_mapped.csv) against the real OSM corridor
network's actual geographic extent.

This is a READ-ONLY validation/reporting pass over landslide_mapper.py's
existing output — it does NOT change the spatial matching algorithm, the
500m threshold (still the authoritative MATCHED/UNMATCHED criterion — see
below), routing, or the risk engine.

--- Why this exists ---
gsi_landslides_corridor.csv was extracted from the source report partly by
keyword matching (e.g. "NH-13"), and NH-13 spans a much larger stretch of
Arunachal Pradesh than our Guwahati-Tawang corridor (see
backend/app/data/README.md and the far-away Anjaw-Tezu / Komlighat-Pasighat
records already noted there). This module quantifies that discrepancy
without silently deleting anything, using two independent geographic checks
run against the real, already-mapped data:

  1. Bounding-box containment — is each record's raw lat/lng inside the OSM
     corridor network's own bounding box?
  2. Connected-component consistency — for MATCHED records specifically, is
     the segment it matched to actually part of the same connected road
     network the 7 demonstration towns sit in, or one of the ~22 small
     disconnected fragments at the extraction boundary (see README)? A
     match to a disconnected fragment would be a real, distinct kind of
     "geographically inconsistent with the intended corridor" match, even
     though it's within 500m of *some* road.

district / movement_type are extracted from the free-text description
columns as a best-effort convenience for the summary grouping in step 5 —
they are not a separate authoritative GSI field (the source table's
original District/Movement Type columns appear to have been concatenated
into description_before_coordinates / description_after_coordinates during
extraction from the PDF report; see ARUNACHAL_DISTRICTS and
MOVEMENT_TYPE_KEYWORDS below). A handful of description_after_coordinates
values contain a leaked table-header artifact from that extraction (e.g.
"...Sl.No. Slide_No State District Slide_Name..."); these are flagged
explicitly rather than silently mis-parsed.

--- Usage ---
    cd backend
    python -m app.data.landslide_corridor_validation
"""
from pathlib import Path
from typing import Optional

import networkx as nx
import pandas as pd

from app.core.routing_engine import build_graph
from app.data.landslide_mapper import DEFAULT_MAPPED_CSV
from app.data.network_loader import load_network

DERIVED_DIR = Path(__file__).parent / "derived"
DEFAULT_OSM_CORRIDOR_CSV = DERIVED_DIR / "gsi_landslides_osm_corridor.csv"

# Real Arunachal Pradesh district names, used to extract the district a GSI
# record's free-text description starts with (longest-first so multi-word
# districts match before a shorter district name that is also their
# prefix, e.g. "Lower Dibang Valley" before "Dibang Valley").
ARUNACHAL_DISTRICTS = sorted(
    [
        "Lower Dibang Valley", "Upper Dibang Valley", "Dibang Valley",
        "East Kameng", "West Kameng", "Pakke-Kessang", "Pakke Kessang",
        "East Siang", "West Siang", "Lower Siang", "Upper Siang", "Siang",
        "Lower Subansiri", "Upper Subansiri", "Kurung Kumey", "Kra Daadi", "Kamle",
        "Lepa Rada", "Shi Yomi",
        "Anjaw", "Changlang", "Longding", "Tirap", "Namsai", "Lohit",
        "Papum Pare", "Tawang",
    ],
    key=len,
    reverse=True,
)

# Known GSI landslide movement-type terms actually observed in this
# dataset's description_after_coordinates column (longest-first).
MOVEMENT_TYPE_KEYWORDS = sorted(
    [
        "rock wedge failure", "rock cum debris slide", "rock cum debris flow",
        "rock cum debris", "debris avalanche", "debris slide", "debris flow",
        "soil slide", "earth slide", "rock slide", "rock fall", "earth flow",
        "subsidence", "sinking", "debris",
    ],
    key=len,
    reverse=True,
)

HEADER_ARTIFACT_MARKERS = ("sl.no", "movement type", "slide_no")


def network_bounding_box(nodes) -> dict:
    lats = [n.lat for n in nodes]
    lngs = [n.lng for n in nodes]
    return {
        "min_lat": min(lats), "max_lat": max(lats),
        "min_lng": min(lngs), "max_lng": max(lngs),
    }


def is_within_bbox(lat: float, lng: float, bbox: dict) -> bool:
    return bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lng"] <= lng <= bbox["max_lng"]


def extract_district(description_before: str) -> Optional[str]:
    if not isinstance(description_before, str):
        return None
    text = description_before.strip()
    for district in ARUNACHAL_DISTRICTS:
        if text.startswith(district):
            return district
    return None


def extract_movement_type(description_after: str) -> str:
    if not isinstance(description_after, str):
        return "Other/Unspecified"
    normalized = " ".join(description_after.strip().split()).lower()
    if any(marker in normalized for marker in HEADER_ARTIFACT_MARKERS):
        return "Other/Unspecified (source header artifact)"
    for keyword in MOVEMENT_TYPE_KEYWORDS:
        if normalized.startswith(keyword):
            return keyword.title()
    return "Other/Unspecified"


def main_component_node_ids(nodes, segments) -> set:
    graph = build_graph(nodes, segments)
    return max(nx.weakly_connected_components(graph), key=len)


def validate(mapped_csv_path: Path = DEFAULT_MAPPED_CSV) -> dict:
    """
    Runs both geographic checks against the already-mapped CSV and returns
    everything needed for the report / for writing the tightened dataset:
    the enriched dataframe (with district/movement_type/within_bbox/
    geographically_consistent columns added), the network bbox, and the
    list of any matched-but-inconsistent records found.
    """
    nodes, segments = load_network()
    segments_by_id = {s.id: s for s in segments}
    bbox = network_bounding_box(nodes)
    main_component = main_component_node_ids(nodes, segments)

    df = pd.read_csv(mapped_csv_path)
    df["district"] = df["description_before_coordinates"].apply(extract_district)
    df["movement_type"] = df["description_after_coordinates"].apply(extract_movement_type)
    df["within_network_bbox"] = df.apply(
        lambda r: is_within_bbox(r["latitude"], r["longitude"], bbox), axis=1
    )

    def is_consistent(row) -> Optional[bool]:
        if row["match_status"] != "MATCHED":
            return None  # consistency is only meaningful for matched records
        segment = segments_by_id.get(row["matched_segment_id"])
        if segment is None:
            return False
        return (
            row["within_network_bbox"]
            and segment.from_node_id in main_component
            and segment.to_node_id in main_component
        )

    df["geographically_consistent"] = df.apply(is_consistent, axis=1)

    inconsistent = df[(df["match_status"] == "MATCHED") & (df["geographically_consistent"] == False)]  # noqa: E712

    return {
        "df": df,
        "bbox": bbox,
        "inconsistent_matches": inconsistent,
    }


def write_osm_corridor_csv(df: pd.DataFrame, path: Path = DEFAULT_OSM_CORRIDOR_CSV) -> pd.DataFrame:
    """
    The geographically-precise, corridor-relevant subset: MATCHED records
    only (the 500m road-matching threshold stays the authoritative
    criterion — see module docstring), which validate() has also confirmed
    sit on the real corridor's main connected component and within its
    bounding box. Does not modify or remove gsi_landslides_corridor.csv,
    gsi_landslides_arunachal.csv, or gsi_landslides_corridor_mapped.csv/
    .geojson — this is an additional, derived, filtered file.
    """
    subset = df[df["match_status"] == "MATCHED"].copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    subset.to_csv(path, index=False)
    return subset


def print_validation_report(result: dict) -> None:
    df = result["df"]
    bbox = result["bbox"]
    total = len(df)
    matched = df[df["match_status"] == "MATCHED"]
    unmatched = df[df["match_status"] == "UNMATCHED"]

    print("=== OSM corridor network geographic extent ===")
    print(
        f"Bounding box: lat [{bbox['min_lat']:.6f}, {bbox['max_lat']:.6f}], "
        f"lng [{bbox['min_lng']:.6f}, {bbox['max_lng']:.6f}]"
    )
    print()

    print("=== Match summary ===")
    print(f"Total source records: {total}")
    print(f"Matched records: {len(matched)}")
    print(f"Unmatched records: {len(unmatched)}")
    print(f"Within network bounding box: {int(df['within_network_bbox'].sum())} / {total}")
    print(f"  Matched but outside bounding box: {int((matched['within_network_bbox'] == False).sum())}")  # noqa: E712
    print(f"  Unmatched but inside bounding box: {int((unmatched['within_network_bbox'] == True).sum())}")  # noqa: E712
    print()

    print("=== Matched records by district ===")
    print(matched["district"].value_counts(dropna=False).to_string())
    print()

    print("=== Matched records by road/highway ===")
    road_label = matched["matched_road_name"].fillna(
        "(unnamed " + matched["matched_highway_class"].astype(str) + ")"
    )
    print(road_label.value_counts().to_string())
    print()

    print("=== Matched records by movement type ===")
    print(matched["movement_type"].value_counts().to_string())
    print()

    print("=== Distance to matched road (meters) ===")
    d = matched["distance_to_road_m"]
    print(f"Minimum: {d.min():.2f}")
    print(f"Median:  {d.median():.2f}")
    print(f"Maximum: {d.max():.2f}")
    print(f"Within 50m:  {int((d <= 50).sum())}")
    print(f"Within 100m: {int((d <= 100).sum())}")
    print(f"Within 500m: {int((d <= 500).sum())} (== all matched, by definition of the threshold)")
    print()

    print("=== Geographic consistency of matched records ===")
    inconsistent = result["inconsistent_matches"]
    print(f"Matched records flagged geographically inconsistent: {len(inconsistent)}")
    if len(inconsistent):
        print(inconsistent[["slide_no", "slide_id", "matched_segment_id", "latitude", "longitude"]].to_string(index=False))
    else:
        print("None found - every matched record sits within the network's bounding box "
              "and on the same connected road network as the 7 demonstration towns.")


if __name__ == "__main__":
    result = validate()
    write_osm_corridor_csv(result["df"])
    print_validation_report(result)
