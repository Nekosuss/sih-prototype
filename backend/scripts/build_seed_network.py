"""
Data-provenance script — NOT part of the running app.

SUPERSEDED as of Part 4 by fetch_osm_corridor_graph.py +
build_branched_seed_network.py, which replace this single-chain,
6-town-to-town-OSRM-route corridor with a real branched OSM road network
(genuine alternate paths). Kept for history/reproducibility of the original
Part 2/3 dataset — not run by anything else in the app.

Transforms _fetched_cache/corridor_data.json (produced by
fetch_corridor_data.py) into backend/app/data/ner_road_network.json, in the
schema app/models/network.py expects.

Fields written as REAL (derived from OpenStreetMap-based sources):
  - node lat/lng            <- Nominatim geocoding
  - node elevation_m         <- Open-Elevation (SRTM-derived)
  - segment geometry          <- OSRM route polyline (real road-following
                                  shape), simplified with Douglas-Peucker
  - segment distance_km,
    estimated_travel_time_min <- OSRM route distance/duration
  - segment slope_deg,
    elevation_m (midpoint)    <- derived from the real endpoint elevations

Fields written as PROTOTYPE-AUTHORED PLACEHOLDERS (NOT from a hazard dataset):
  - terrain_type              <- assigned from general regional knowledge
  - landslide_susceptibility  <- illustrative placeholder
  - flood_susceptibility      <- illustrative placeholder
  - base_risk                 <- formula over the placeholders above

Each segment's "source" block in the output JSON states this per field, and
the top-level "_provenance" block documents dataset-wide sourcing and the
OpenStreetMap attribution requirement (ODbL). See backend/app/data/README.md.

Usage:
    python fetch_corridor_data.py   # refresh the cache (needs network access)
    python build_seed_network.py    # regenerate the seed JSON from the cache
"""
import json
import math
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "_fetched_cache"
OUT_PATH = Path(__file__).parent.parent / "app" / "data" / "ner_road_network.json"

data = json.loads((CACHE_DIR / "corridor_data.json").read_text())
towns = {t["id"]: t for t in data["towns"]}
elevations = data["elevations"]
routes = data["routes"]


def rdp(points, epsilon):
    """Ramer-Douglas-Peucker polyline simplification. points: list of (lon, lat)."""
    if len(points) < 3:
        return points

    def perp_dist(pt, start, end):
        if start == end:
            return math.hypot(pt[0] - start[0], pt[1] - start[1])
        x, y = pt
        x1, y1 = start
        x2, y2 = end
        num = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
        den = math.hypot(y2 - y1, x2 - x1)
        return num / den

    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        d = perp_dist(points[i], points[0], points[-1])
        if d > dmax:
            index, dmax = i, d

    if dmax > epsilon:
        left = rdp(points[: index + 1], epsilon)
        right = rdp(points[index:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ~0.0003 deg ~= 30m at this latitude — keeps hairpin switchbacks on the
# mountain sections while dropping redundant near-straight-line points.
EPSILON = 0.0003

# Terrain classification + hazard placeholders. PROTOTYPE ESTIMATES — see
# module docstring and backend/app/data/README.md before treating these as
# real hazard data.
SEGMENT_META = {
    "guwahati__tezpur": dict(terrain_type="plain", landslide_susceptibility=0.05, flood_susceptibility=0.35, road_name="NH27 / NH15 (Guwahati-Tezpur)"),
    "tezpur__bhalukpong": dict(terrain_type="plain", landslide_susceptibility=0.15, flood_susceptibility=0.45, road_name="NH13 (Tezpur-Bhalukpong approach)"),
    "bhalukpong__bomdila": dict(terrain_type="mountain", landslide_susceptibility=0.7, flood_susceptibility=0.2, road_name="NH13 (Bhalukpong-Bomdila climb)"),
    "bomdila__dirang": dict(terrain_type="mountain", landslide_susceptibility=0.55, flood_susceptibility=0.1, road_name="NH13 (Bomdila-Dirang)"),
    "dirang__sela_pass": dict(terrain_type="mountain", landslide_susceptibility=0.6, flood_susceptibility=0.05, road_name="NH13 (Dirang-Sela Pass climb)"),
    "sela_pass__tawang": dict(terrain_type="mountain", landslide_susceptibility=0.65, flood_susceptibility=0.05, road_name="NH13 (Sela Pass-Tawang descent)"),
}


def compute_base_risk(terrain_type, landslide_susc, flood_susc):
    terrain_base = {"plain": 0.05, "hill": 0.15, "mountain": 0.25}[terrain_type]
    return round(min(1.0, terrain_base + 0.4 * landslide_susc + 0.2 * flood_susc), 3)


def main():
    nodes_out = [
        {
            "id": t["id"],
            "name": t["name"],
            "lat": t["lat"],
            "lng": t["lng"],
            "type": t["type"],
            "elevation_m": elevations[t["id"]],
        }
        for t in data["towns"]
    ]

    segments_out = []
    town_order = [t["id"] for t in data["towns"]]
    for a_id, b_id in zip(town_order, town_order[1:]):
        key = f"{a_id}__{b_id}"
        route = routes[key]
        meta = SEGMENT_META[key]

        raw_pts = [(lon, lat) for lon, lat in route["geometry_lonlat"]]
        simplified = rdp(raw_pts, EPSILON)
        geometry = [{"lat": lat, "lng": lon} for lon, lat in simplified]

        distance_km = round(route["distance_m"] / 1000.0, 2)
        duration_min = round(route["duration_s"] / 60.0, 1)

        elev_a, elev_b = elevations[a_id], elevations[b_id]
        elevation_gain_m = round(elev_b - elev_a, 1)
        slope_deg = round(math.degrees(math.atan2(abs(elevation_gain_m), distance_km * 1000.0)), 2)
        mid_elevation_m = round((elev_a + elev_b) / 2.0, 1)

        base_risk = compute_base_risk(meta["terrain_type"], meta["landslide_susceptibility"], meta["flood_susceptibility"])

        d_start = haversine_km(geometry[0]["lat"], geometry[0]["lng"], towns[a_id]["lat"], towns[a_id]["lng"])
        d_end = haversine_km(geometry[-1]["lat"], geometry[-1]["lng"], towns[b_id]["lat"], towns[b_id]["lng"])

        segments_out.append({
            "id": f"seg_{a_id}_{b_id}",
            "from_node_id": a_id,
            "to_node_id": b_id,
            "name": meta["road_name"],
            "road_type": "primary",  # closest RoadType match for a national-highway corridor leg
            "distance_km": distance_km,
            "estimated_travel_time_min": duration_min,
            "geometry": geometry,
            "terrain_type": meta["terrain_type"],
            "slope_deg": slope_deg,
            "elevation_m": mid_elevation_m,
            "landslide_susceptibility": meta["landslide_susceptibility"],
            "flood_susceptibility": meta["flood_susceptibility"],
            "base_risk": base_risk,
            "status": "open",
            "current_risk_score": base_risk,
            "source": {
                "geometry_distance_duration": "real: OpenStreetMap-derived, via OSRM public routing API (router.project-osrm.org), simplified with Douglas-Peucker (epsilon=0.0003 deg)",
                "elevation": "real: Open-Elevation API (SRTM-derived), from segment endpoint elevations",
                "terrain_type": "prototype_estimate: assigned from general regional knowledge, not a verified GIS layer",
                "landslide_susceptibility": "prototype_estimate: placeholder, not from a hazard-zonation dataset",
                "flood_susceptibility": "prototype_estimate: placeholder, not from a hazard-zonation dataset",
                "base_risk": "prototype_estimate: computed from the placeholder fields above",
            },
            "_endpoint_snap_distance_km": {"start": round(d_start, 2), "end": round(d_end, 2)},
        })

    out = {
        "_provenance": {
            "description": "NER Guwahati-Tawang demonstration corridor seed dataset for the SIH hazard-aware-routing prototype.",
            "node_coordinates": "OpenStreetMap Nominatim geocoding API (nominatim.openstreetmap.org).",
            "node_elevation": "Open-Elevation API (api.open-elevation.com, SRTM-derived).",
            "segment_geometry_distance_duration": "OSRM public demo routing server (router.project-osrm.org), driving profile. OSRM routes over OpenStreetMap road data.",
            "attribution_required": "Map data (c) OpenStreetMap contributors, ODbL 1.0 license (https://www.openstreetmap.org/copyright). Any map view built from this data must display that attribution.",
            "prototype_estimated_fields": "terrain_type, landslide_susceptibility, flood_susceptibility, base_risk are PROTOTYPE PLACEHOLDER values, NOT sourced from a verified hazard dataset. Must be replaced with real data (e.g. GSI Bhukosh/BhuVigyan landslide susceptibility layers, NRSC/CWC flood hazard atlases) before any real-world hazard claim.",
            "osrm_demo_server_notice": "router.project-osrm.org is OSRM's free public demo server, rate-limited and not intended for production use. A production build should self-host OSRM/an equivalent router or use a licensed routing API.",
            "regeneration": "Regenerate with backend/scripts/fetch_corridor_data.py then backend/scripts/build_seed_network.py.",
        },
        "nodes": nodes_out,
        "segments": segments_out,
    }

    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    for s in segments_out:
        print(s["id"], "pts=", len(s["geometry"]), "dist_km=", s["distance_km"], "snap(start,end)=", s["_endpoint_snap_distance_km"])


if __name__ == "__main__":
    main()
