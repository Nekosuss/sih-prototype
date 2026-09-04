"""
Tests for the read-only validation/reporting pass over the already-mapped
GSI landslide dataset (app/data/landslide_corridor_validation.py).

Does not touch the spatial matching algorithm, routing, or the risk engine
— only exercises the extra bounding-box / connected-component consistency
checks and the district/movement_type text extraction.
"""
import pandas as pd
import pytest

from app.data.landslide_corridor_validation import (
    extract_district,
    extract_movement_type,
    is_within_bbox,
    network_bounding_box,
    validate,
    write_osm_corridor_csv,
)

BBOX = {"min_lat": 26.0, "max_lat": 28.0, "min_lng": 91.5, "max_lng": 93.0}


def test_network_bounding_box_matches_real_node_extent(network):
    nodes, _ = network
    bbox = network_bounding_box(nodes)
    assert bbox["min_lat"] == min(n.lat for n in nodes)
    assert bbox["max_lat"] == max(n.lat for n in nodes)
    assert bbox["min_lng"] == min(n.lng for n in nodes)
    assert bbox["max_lng"] == max(n.lng for n in nodes)
    # sanity: this is the Guwahati-Tawang corridor, not somewhere else
    assert 25 < bbox["min_lat"] < bbox["max_lat"] < 29
    assert 90 < bbox["min_lng"] < bbox["max_lng"] < 94


def test_is_within_bbox():
    assert is_within_bbox(27.0, 92.0, BBOX) is True
    assert is_within_bbox(29.0, 92.0, BBOX) is False  # north of the box
    assert is_within_bbox(27.0, 96.0, BBOX) is False  # east of the box (like the far Anjaw-Tezu records)


def test_extract_district_known_cases():
    assert extract_district("West Kameng Slide at Tippi") == "West Kameng"
    assert extract_district("Longding 800 m S of Bonia") == "Longding"
    # a district whose name is a prefix of a longer district name must not
    # shadow the longer, more specific match
    assert extract_district("Lower Dibang Valley some place") == "Lower Dibang Valley"
    assert extract_district("Pakke Kessang NH 13") == "Pakke Kessang"


def test_extract_district_unknown_returns_none():
    assert extract_district("Nowhereland some slide") is None
    assert extract_district(None) is None


def test_extract_movement_type_known_cases():
    assert extract_movement_type("Debris Slide NA") == "Debris Slide"
    assert extract_movement_type("Debris Slide 1 July 2016 at 12:20 hrs.") == "Debris Slide"
    assert extract_movement_type("Rock Wedge failure NA") == "Rock Wedge Failure"
    assert extract_movement_type("Soil Slide NA") == "Soil Slide"


def test_extract_movement_type_flags_header_artifact():
    leaked = "Debris Slide NA Sl.No. Slide_No State District Slide_Name NH_SH_Location Latitude Longitude Material Involved Movement Type History"
    assert "header artifact" in extract_movement_type(leaked)


def test_extract_movement_type_unknown_falls_back():
    assert extract_movement_type("Something Unrecognized NA") == "Other/Unspecified"
    assert extract_movement_type(None) == "Other/Unspecified"


def test_validate_against_real_mapped_dataset():
    """Read-only pass over the real derived CSV: shape and invariants, not
    brittle exact counts (which would break on any re-run of the mapper)."""
    result = validate()
    df = result["df"]

    assert {"district", "movement_type", "within_network_bbox", "geographically_consistent"} <= set(df.columns)

    matched = df[df["match_status"] == "MATCHED"]
    unmatched = df[df["match_status"] == "UNMATCHED"]
    assert len(matched) > 0
    assert len(unmatched) > 0

    # consistency is only meaningful (non-null) for matched records
    assert matched["geographically_consistent"].notna().all()
    assert unmatched["geographically_consistent"].isna().all()

    # every matched record is, by construction, within 500m of a road that
    # is itself within the network's own bounding box
    assert matched["within_network_bbox"].all()

    # the inconsistent-matches list is a subset of the matched records
    inconsistent = result["inconsistent_matches"]
    assert set(inconsistent["slide_no"]) <= set(matched["slide_no"])


def test_write_osm_corridor_csv_contains_only_matched_records(tmp_path):
    result = validate()
    out_path = tmp_path / "gsi_landslides_osm_corridor.csv"
    subset = write_osm_corridor_csv(result["df"], out_path)

    assert out_path.exists()
    assert (subset["match_status"] == "MATCHED").all()
    assert len(subset) == (result["df"]["match_status"] == "MATCHED").sum()

    reloaded = pd.read_csv(out_path)
    assert len(reloaded) == len(subset)
    assert "district" in reloaded.columns and "movement_type" in reloaded.columns


def test_write_osm_corridor_csv_does_not_touch_source_files():
    """The pipeline must never overwrite the original GSI CSVs or the
    already-mapped dataset — only add a new derived file."""
    from app.data.landslide_mapper import DATA_DIR, DEFAULT_MAPPED_CSV

    corridor_csv = DATA_DIR / "gsi_landslides_corridor.csv"
    arunachal_csv = DATA_DIR / "gsi_landslides_arunachal.csv"
    assert corridor_csv.exists()
    assert arunachal_csv.exists()
    assert DEFAULT_MAPPED_CSV.exists()

    before = corridor_csv.stat().st_mtime
    validate()
    after = corridor_csv.stat().st_mtime
    assert before == after
