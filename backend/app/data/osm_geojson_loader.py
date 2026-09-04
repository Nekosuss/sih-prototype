"""
Generic OSM-GeoJSON -> internal Node/RoadSegment converter.

Converts a GeoJSON FeatureCollection of OSM road LineStrings (properties:
osm_id, name, highway, ref, oneway, maxspeed — the shape produced by a
typical Overpass/OSM road extract) into this project's Node/RoadSegment
model objects, ready for routing_engine.build_graph(). This is the ONLY
module that parses OSM/GeoJSON data — routing_engine.py never sees a
feature, a tag, or a coordinate list; it only ever consumes Node/RoadSegment
objects (see routing_engine.py's module docstring).

This module knows nothing about "Guwahati" or "Tawang" specifically: it is
a generic converter. Corridor-specific concerns (which named locations to
tag with a friendly name, their real reference elevations) are passed in by
the caller as plain dicts (see demo_locations.py / network_loader.py) — a
different NER-region GeoJSON extract can be substituted later by pointing
network_loader.py at a new file and a new named-locations list, without
touching this module or routing_engine.

--- Topology: why this isn't just "one edge per feature" ---

A real OSM way's LineString often runs THROUGH another way's intersection
without an explicit shared endpoint — a side road's T-junction onto a
highway is typically a point in the MIDDLE of the highway's coordinate
list, not at the highway way's start/end. Treating each feature as exactly
one edge between its first and last coordinate would miss almost all real
branching. So this loader:
  1. Finds every coordinate that is either a way's own endpoint, or shared
     with another way's coordinate (a real intersection).
  2. Splits each way's LineString into one edge per run between consecutive
     such points.
That splitting is what surfaces genuine branching in the resulting graph —
see tests/test_network.py::test_network_has_genuine_branching.

--- What's real vs. derived vs. not-yet-assessed in the output ---

REAL (straight from the GeoJSON): node lat/lng, segment geometry,
osm_way_ids, name, road_type (from `highway`), ref, oneway, maxspeed.

REAL DISTANCE, ASSUMED SPEED: distance_km is computed from real coordinates
(haversine sum). estimated_travel_time_min divides that by an assumed speed:
the tagged `maxspeed` where parseable, otherwise a highway-class default
(DEFAULT_SPEED_KPH below) — the same kind of assumption any router makes
for untagged ways. assumed_speed_kph is exposed on RoadSegment so this is
never hidden inside a pre-computed number.

REAL DEM-DERIVED (Part 4.8): elevation_m/slope_deg. When a `dem` (see
dem_loader.py) is supplied, each segment's geometry is resampled and
queried against a real SRTM1-derived DEM (dem_processor.py) to produce a
representative elevation (mean of sampled points along the segment) and a
slope (mean absolute gradient magnitude along the sampled profile — not
just the endpoint-to-endpoint grade, so a climb-then-descend segment isn't
misread as flat). terrain_type is then a threshold classification of that
real elevation. See dem_processor.py's module docstring and
backend/app/data/README.md for exact methodology, units and limitations.

FALLBACK (only when the DEM has no usable data for a specific segment's
geometry, e.g. a coordinate outside the cached tiles): elevation_m falls
back to the old nearest-reference-town approximation, slope_deg is left
None, and RoadSegment.source explicitly says "fallback", never claiming a
real per-segment measurement it doesn't have. When no `dem` is supplied at
all (dem=None), every segment uses this same approximation, unchanged from
Part 4 behaviour.

NOT ASSESSED (explicitly not fabricated): landslide_susceptibility and
flood_susceptibility are uniformly 0.0 — no real hazard-zonation dataset is
available yet. base_risk reuses the existing
core/risk_engine.py::compute_base_risk formula, which reduces to the
terrain-only component since susceptibility factors are 0.
"""
import json
import math
from pathlib import Path
from typing import Optional

from app.core.geo import haversine_km
from app.core.risk_engine import compute_base_risk
from app.data.dem_loader import DEM_SOURCE_NAME
from app.data.dem_processor import compute_segment_terrain
from app.models.network import GeoPoint, Node, NodeType, RoadSegment, RoadType, TerrainType

COORD_DECIMALS = 6  # ~0.11m at this latitude; matches this dataset's actual precision

# Highway-class default speed assumptions (km/h), used only when a way has
# no parseable `maxspeed` tag. Deliberately simple and explicitly an
# assumption, not measured data — see module docstring and
# backend/app/data/README.md limitations section (mountain hairpins on this
# corridor will be much slower than these flat-road defaults suggest).
DEFAULT_SPEED_KPH = {
    "trunk": 50.0,
    "trunk_link": 30.0,
    "primary": 40.0,
    "primary_link": 25.0,
    "secondary": 35.0,
    "secondary_link": 25.0,
    "tertiary": 30.0,
    "tertiary_link": 20.0,
    "unclassified": 25.0,
}
FALLBACK_SPEED_KPH = 25.0

NAMED_LOCATION_SNAP_TOLERANCE_KM = 10.0


def _round_coord(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon, COORD_DECIMALS), round(lat, COORD_DECIMALS))


def _parse_maxspeed_kph(raw: Optional[str]) -> Optional[float]:
    """OSM maxspeed values are usually a plain number in km/h (this
    dataset's are); defensively ignore anything that doesn't parse as a
    plain number rather than guessing at a unit."""
    if not raw:
        return None
    try:
        return float(str(raw).strip().split()[0])
    except (ValueError, IndexError):
        return None


def _is_bidirectional(oneway_raw: Optional[str]) -> bool:
    """Only the exact tag value "yes" makes a road one-way; every other
    value (None, "no", "reversible", or anything else) stays bidirectional.
    This is a deliberate simplification (a real "reversible" road is
    direction-restricted at certain times, which this static graph can't
    represent) — see backend/app/data/README.md limitations."""
    return (oneway_raw or "").strip().lower() != "yes"


def classify_terrain(elevation_m: float) -> TerrainType:
    if elevation_m < 300:
        return TerrainType.plain
    if elevation_m < 1500:
        return TerrainType.hill
    return TerrainType.mountain


def _nearest_reference(lat: float, lng: float, reference_points: list[dict]) -> dict:
    return min(reference_points, key=lambda r: haversine_km(lat, lng, r["lat"], r["lng"]))


def _road_type_for(highway: Optional[str]) -> RoadType:
    try:
        return RoadType(highway)
    except ValueError:
        return RoadType.unclassified


def parse_geojson_to_network(
    path: Path,
    named_locations: Optional[list[dict]] = None,
    dem=None,
) -> tuple[list[Node], list[RoadSegment]]:
    """
    named_locations: optional list of {"name", "type", "lat", "lng",
    "elevation_m"} dicts (see demo_locations.py). Each is matched to its
    nearest graph node (nearest-node resolution), and that node is tagged
    with the given name/type/elevation — it is NOT relabeled to a new id,
    so it stays a normal, uniquely-identified graph node that also happens
    to be resolvable by its friendly name via routing_engine.resolve_location
    (which matches on node id, then node name). Also used as the FALLBACK
    reference points for elevation when no `dem` is supplied, or when the
    DEM has no usable data for a specific segment — see module docstring.

    dem: optional object with an elevation_at(lat, lon) -> Optional[float]
    method (see dem_loader.DemLoader / dem_loader.get_default_dem_loader()).
    When supplied, elevation_m/slope_deg are derived from it via
    dem_processor.compute_segment_terrain() — real per-segment DEM sampling
    rather than the nearest-reference-town approximation. Passing None
    (the default) keeps the old approximation-only behaviour, e.g. for
    callers/tests that don't want to touch the DEM.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    features = raw["features"]

    # --- Pass 1: find every coordinate that must become a graph node ---
    coord_touch_count: dict[tuple[float, float], int] = {}
    node_coords: set[tuple[float, float]] = set()

    for feature in features:
        coords = feature["geometry"]["coordinates"]
        if len(coords) < 2:
            continue  # degenerate feature, not a usable road segment
        node_coords.add(_round_coord(*coords[0]))
        node_coords.add(_round_coord(*coords[-1]))
        for lon, lat in coords:
            key = _round_coord(lon, lat)
            coord_touch_count[key] = coord_touch_count.get(key, 0) + 1

    for key, count in coord_touch_count.items():
        if count >= 2:
            node_coords.add(key)

    # --- Assign stable, deterministic node ids ---
    ordered_coords = sorted(node_coords, key=lambda c: (c[1], c[0]))  # by (lat, lon)
    node_id_by_coord = {coord: f"n{i:05d}" for i, coord in enumerate(ordered_coords)}

    nodes_by_id: dict[str, dict] = {
        node_id: {"id": node_id, "name": None, "lat": lat, "lng": lon, "type": NodeType.junction, "elevation_m": None}
        for (lon, lat), node_id in node_id_by_coord.items()
    }

    # --- Tag the named demonstration locations onto their nearest node ---
    reference_points = list(named_locations) if named_locations else []
    if named_locations:
        node_list_for_lookup = list(nodes_by_id.values())
        for loc in named_locations:
            nearest = min(
                node_list_for_lookup,
                key=lambda n: haversine_km(loc["lat"], loc["lng"], n["lat"], n["lng"]),
            )
            dist_km = haversine_km(loc["lat"], loc["lng"], nearest["lat"], nearest["lng"])
            if dist_km > NAMED_LOCATION_SNAP_TOLERANCE_KM:
                raise ValueError(
                    f"Named location {loc['name']!r} has no graph node within "
                    f"{NAMED_LOCATION_SNAP_TOLERANCE_KM}km (nearest is {dist_km:.2f}km away) — "
                    f"the GeoJSON likely doesn't cover this location"
                )
            nearest["name"] = loc["name"]
            nearest["type"] = NodeType(loc["type"])
            nearest["elevation_m"] = loc["elevation_m"]

    if not reference_points:
        # No terrain reference points supplied — fall back to a single
        # global "unknown elevation" assumption rather than crashing.
        reference_points = [{"lat": 0.0, "lng": 0.0, "elevation_m": 300.0}]

    # --- Pass 2: split each feature into edges between consecutive node points ---
    segments_by_pair: dict[frozenset, RoadSegment] = {}

    for feature in features:
        props = feature["properties"]
        coords = feature["geometry"]["coordinates"]
        if len(coords) < 2:
            continue

        highway = props.get("highway")
        road_type = _road_type_for(highway)
        name = props.get("name")
        ref = props.get("ref")
        oneway_raw = props.get("oneway")
        maxspeed_raw = props.get("maxspeed")
        bidirectional = _is_bidirectional(oneway_raw)
        try:
            osm_id = int(props["osm_id"])
        except (KeyError, TypeError, ValueError):
            osm_id = None

        run_start = 0
        run_index = 0
        for i in range(1, len(coords)):
            if _round_coord(*coords[i]) not in node_coords:
                continue
            run_coords = coords[run_start : i + 1]
            if len(run_coords) >= 2:
                from_id = node_id_by_coord[_round_coord(*run_coords[0])]
                to_id = node_id_by_coord[_round_coord(*run_coords[-1])]
                if from_id != to_id:
                    segment = _build_segment(
                        seg_id=f"seg_{osm_id}_{run_index}",
                        from_id=from_id,
                        to_id=to_id,
                        run_coords=run_coords,
                        road_type=road_type,
                        name=name,
                        ref=ref,
                        oneway_raw=oneway_raw,
                        maxspeed_raw=maxspeed_raw,
                        bidirectional=bidirectional,
                        highway=highway,
                        osm_id=osm_id,
                        reference_points=reference_points,
                        dem=dem,
                    )
                    # Two different ways can connect the same pair of nodes
                    # (e.g. separately-digitized carriageways of a divided
                    # highway). routing_engine.build_graph() builds a plain
                    # (Di)Graph with one edge per node pair, so keep only
                    # the shortest of any such duplicates.
                    pair_key = frozenset((from_id, to_id))
                    existing = segments_by_pair.get(pair_key)
                    if existing is None or segment.distance_km < existing.distance_km:
                        segments_by_pair[pair_key] = segment
                    run_index += 1
            run_start = i

    nodes = [Node(**n) for n in nodes_by_id.values()]
    segments = list(segments_by_pair.values())
    return nodes, segments


def _build_segment(
    seg_id: str,
    from_id: str,
    to_id: str,
    run_coords: list[list[float]],
    road_type: RoadType,
    name: Optional[str],
    ref: Optional[str],
    oneway_raw: Optional[str],
    maxspeed_raw: Optional[str],
    bidirectional: bool,
    highway: Optional[str],
    osm_id: Optional[int],
    reference_points: list[dict],
    dem=None,
) -> RoadSegment:
    geometry = [GeoPoint(lat=lat, lng=lon) for lon, lat in run_coords]

    distance_km = sum(
        haversine_km(run_coords[i][1], run_coords[i][0], run_coords[i + 1][1], run_coords[i + 1][0])
        for i in range(len(run_coords) - 1)
    )

    tagged_speed = _parse_maxspeed_kph(maxspeed_raw)
    assumed_speed_kph = tagged_speed or DEFAULT_SPEED_KPH.get(highway, FALLBACK_SPEED_KPH)
    estimated_travel_time_min = (distance_km / assumed_speed_kph) * 60.0 if assumed_speed_kph else 0.0

    mid = geometry[len(geometry) // 2]
    slope_deg: Optional[float] = None

    if dem is not None:
        terrain = compute_segment_terrain(run_coords, dem)
        if terrain.elevation_m is not None:
            elevation_m = terrain.elevation_m
            slope_deg = terrain.slope_deg
            elevation_source = (
                f"real: DEM sample ({DEM_SOURCE_NAME}), mean of "
                f"{terrain.valid_sample_count}/{terrain.sample_count} points along geometry "
                f"(range {terrain.min_elevation_m:.1f}-{terrain.max_elevation_m:.1f}m)"
            )
            slope_source = (
                f"real: mean absolute gradient over the DEM elevation profile "
                f"({terrain.valid_sample_count} points, see dem_processor.py)"
                if slope_deg is not None
                else "insufficient_dem_samples: only one usable DEM point along this segment, cannot derive slope"
            )
        else:
            # DEM supplied but had no usable (non-void, in-coverage) data
            # anywhere along this specific segment's geometry — fall back,
            # and say so honestly rather than silently claiming a real
            # per-segment measurement.
            nearest_ref = _nearest_reference(mid.lat, mid.lng, reference_points)
            elevation_m = nearest_ref["elevation_m"]
            elevation_source = (
                "fallback: DEM had no usable data for this segment's geometry "
                "(outside cached tile coverage or void); used nearest-reference-town "
                "approximation — NOT a real per-segment measurement"
            )
            slope_source = "unavailable: DEM had no usable data for this segment"
    else:
        nearest_ref = _nearest_reference(mid.lat, mid.lng, reference_points)
        elevation_m = nearest_ref["elevation_m"]
        elevation_source = "derived: nearest-reference-point approximation, not measured for this segment"
        slope_source = "not_computed: no DEM supplied to the loader"

    terrain_type = classify_terrain(elevation_m)

    landslide_susceptibility = 0.0
    flood_susceptibility = 0.0
    base_risk = compute_base_risk(terrain_type, landslide_susceptibility, flood_susceptibility)

    return RoadSegment(
        id=seg_id,
        from_node_id=from_id,
        to_node_id=to_id,
        name=name,
        road_type=road_type,
        distance_km=round(distance_km, 4),
        estimated_travel_time_min=round(estimated_travel_time_min, 2),
        geometry=geometry,
        ref=ref,
        oneway=oneway_raw,
        maxspeed=maxspeed_raw,
        bidirectional=bidirectional,
        assumed_speed_kph=round(assumed_speed_kph, 1) if assumed_speed_kph else None,
        terrain_type=terrain_type,
        slope_deg=round(slope_deg, 2) if slope_deg is not None else None,
        elevation_m=round(elevation_m, 1),
        landslide_susceptibility=landslide_susceptibility,
        flood_susceptibility=flood_susceptibility,
        base_risk=base_risk,
        status="open",
        current_risk_score=base_risk,
        osm_way_ids=[osm_id] if osm_id is not None else [],
        source={
            "geometry_distance": "real: OpenStreetMap way geometry (GeoJSON extract)",
            "road_type_name_ref_oneway_maxspeed": "real: OSM highway/name/ref/oneway/maxspeed tags, preserved as-is",
            "estimated_travel_time_min": "real distance over a real-tagged-or-highway-class-default assumed speed (see assumed_speed_kph)",
            "elevation_m": elevation_source,
            "slope_deg": slope_source,
            "terrain_type": "derived: elevation-threshold heuristic over the elevation above",
            "landslide_susceptibility": "not_assessed: uniform placeholder 0.0, no real hazard-zonation dataset yet",
            "flood_susceptibility": "not_assessed: uniform placeholder 0.0, no real hazard-zonation dataset yet",
            "base_risk": "derived: terrain-only (susceptibility factors are 0 pending real data); does NOT yet factor in slope_deg",
        },
    )
