"""
Loads the road network into Node/RoadSegment objects.

This is deliberately the *only* place in the backend that knows which file
the network currently comes from (a real OSM GeoJSON extract — see
guwahati_tawang_osm_corridor.geojson and README.md in this folder for what's
real vs. derived vs. not-yet-assessed) and which named locations to tag on
it (demo_locations.py). The actual GeoJSON parsing/graph-topology logic
lives in osm_geojson_loader.py, which knows nothing about this specific
corridor — swapping in a different NER dataset later means changing the
two constants below, not routing_engine or risk_engine.

routing_engine and risk_engine never read this file (or the GeoJSON)
directly — they only ever receive the Node/RoadSegment objects returned by
load_network().

Part 4.8: this is also where the real DEM gets wired in. get_default_dem_loader()
(dem_loader.py) reads the 4 SRTM1 tiles cached under data/dem_cache/ that
cover this corridor's bounding box — see README.md's "DEM provenance"
section. Pass use_dem=False to get the old nearest-reference-town
approximation instead (e.g. for a fast unit test that doesn't care about
real terrain values).

Part 5: this is also where the previously-unwired GSI landslide features
get merged in. landslide_mapper.py's spatial join was, until now, a
standalone offline pipeline (`python -m app.data.landslide_mapper`) whose
output (derived/road_landslide_features.csv) was never read back by the
running app — every live segment's historical_landslide_count was 0
regardless of the real matched data. That gap is closed here: if the
derived CSV exists and its segment ids match the currently loaded network
exactly, its counts/distances are merged onto the segments. If the file is
missing or looks stale (a different segment set — e.g. a different GeoJSON
was swapped in without re-running the pipeline), the merge is skipped and
segments keep their default 0/None rather than silently applying
mismatched data. Pass use_landslide_features=False to always skip this
(e.g. a test that wants the old always-zero behaviour). This does NOT
touch base_risk/current_risk_score/routing — see risk_engine.py (Part 5)
for the first thing that actually reads these fields.

Part 11: the same missing-derived-file merge pattern is reused for
hazard_layer_mapper.py's landslide/flood hazard-ZONATION features
(derived/road_hazard_layer_features.csv) — see
app/data/hazard_layer_loader.py's module docstring for why that CSV
currently doesn't exist (no official APSAC layer has been obtained) and is
therefore never committed to this repository. Its absence means this merge
is always skipped today, which is the honest, correct outcome — every
segment's landslide_hazard_score/flood_hazard_score simply stays at its
default None ("no official layer available") rather than a fabricated
value. Pass use_hazard_layer_features=False to force-skip this explicitly
(mirroring use_landslide_features=False above).
"""
from pathlib import Path
from typing import Optional

from app.data.demo_locations import DEMO_LOCATIONS
from app.data.dem_loader import get_default_dem_loader
from app.data.osm_geojson_loader import parse_geojson_to_network
from app.models.network import Node, RoadSegment

DEFAULT_DATA_FILE = Path(__file__).parent / "guwahati_tawang_osm_corridor.geojson"


def _maybe_enrich_with_landslide_features(segments: list[RoadSegment]) -> list[RoadSegment]:
    # Local import: landslide_mapper.py itself calls load_network() (to run
    # its own spatial join against a fresh network), so importing it at
    # module level here would be a circular import. Importing lazily,
    # inside the function body, sidesteps that — by the time this runs,
    # both modules are fully initialized.
    import pandas as pd

    from app.data.landslide_mapper import DEFAULT_FEATURES_CSV, enrich_segments_with_landslide_features

    if not DEFAULT_FEATURES_CSV.exists():
        return segments

    features_df = pd.read_csv(DEFAULT_FEATURES_CSV)
    segment_ids = {s.id for s in segments}
    if segment_ids - set(features_df["segment_id"]):
        # The precomputed features don't cover every current segment (e.g.
        # the GeoJSON was swapped without re-running landslide_mapper) —
        # skip rather than guess at partial/stale data.
        return segments

    return enrich_segments_with_landslide_features(segments, features_df)


def _maybe_enrich_with_hazard_layer_features(segments: list[RoadSegment]) -> list[RoadSegment]:
    # Local import for the same reason as above: hazard_layer_mapper.py
    # itself calls load_network() to run its own spatial pass.
    import pandas as pd

    from app.data.hazard_layer_mapper import DEFAULT_FEATURES_CSV, enrich_segments_with_hazard_layer_features

    if not DEFAULT_FEATURES_CSV.exists():
        return segments

    features_df = pd.read_csv(DEFAULT_FEATURES_CSV)
    segment_ids = {s.id for s in segments}
    if segment_ids - set(features_df["segment_id"]):
        return segments

    return enrich_segments_with_hazard_layer_features(segments, features_df)


def load_network(
    path: Path = DEFAULT_DATA_FILE,
    named_locations: list[dict] = DEMO_LOCATIONS,
    use_dem: bool = True,
    dem: Optional[object] = None,
    use_landslide_features: bool = True,
    use_hazard_layer_features: bool = True,
) -> tuple[list[Node], list[RoadSegment]]:
    if dem is None and use_dem:
        dem = get_default_dem_loader()
    elif not use_dem:
        dem = None
    nodes, segments = parse_geojson_to_network(path, named_locations=named_locations, dem=dem)

    node_ids = {n.id for n in nodes}
    for s in segments:
        if s.from_node_id not in node_ids:
            raise ValueError(f"Segment {s.id!r} references unknown from_node_id {s.from_node_id!r}")
        if s.to_node_id not in node_ids:
            raise ValueError(f"Segment {s.id!r} references unknown to_node_id {s.to_node_id!r}")

    if use_landslide_features:
        segments = _maybe_enrich_with_landslide_features(segments)

    if use_hazard_layer_features:
        segments = _maybe_enrich_with_hazard_layer_features(segments)

    return nodes, segments
