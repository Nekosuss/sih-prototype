"""
Data-provenance script — NOT part of the running app.

Re-fetches real, OpenStreetMap-derived data for the Guwahati-Tawang
demonstration corridor: town coordinates (Nominatim geocoding), town
elevations (Open-Elevation / SRTM), and real road-following route geometry,
distance, and duration for each town-to-town leg (OSRM public routing API,
which routes over OpenStreetMap road data).

Run this manually (network access required) to refresh the raw snapshot,
then run build_seed_network.py to regenerate
backend/app/data/ner_road_network.json from it. The app itself only ever
reads that generated JSON file via app/data/network_loader.py — it never
calls these external APIs at runtime.

Usage:
    python fetch_corridor_data.py
Writes ./_fetched_cache/corridor_data.json and per-request raw responses
under ./_fetched_cache/raw/ (gitignored — regenerate rather than commit).
"""
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

UA = "SIH-Hazard-Routing-Prototype/1.0 (student project; contact: agentcott544@gmail.com)"
CACHE_DIR = Path(__file__).parent / "_fetched_cache"
RAW_DIR = CACHE_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Guwahati -> Tawang corridor, in order. Coordinates here are the last known
# Nominatim results (see _fetched_cache/raw/nominatim_*.json after a fresh
# run) — kept inline so the script is runnable without a prior cache.
TOWNS = [
    {"id": "guwahati", "name": "Guwahati", "type": "city", "lat": 26.1805978, "lng": 91.7539430},
    {"id": "tezpur", "name": "Tezpur", "type": "town", "lat": 26.6229928, "lng": 92.7976082},
    {"id": "bhalukpong", "name": "Bhalukpong", "type": "town", "lat": 27.0137235, "lng": 92.6358068},
    {"id": "bomdila", "name": "Bomdila", "type": "town", "lat": 27.2644450, "lng": 92.4206519},
    {"id": "dirang", "name": "Dirang", "type": "town", "lat": 27.3600800, "lng": 92.2412100},
    {"id": "sela_pass", "name": "Sela Pass", "type": "mountain_pass", "lat": 27.5035642, "lng": 92.1044346},
    {"id": "tawang", "name": "Tawang", "type": "city", "lat": 27.5879186, "lng": 91.8637330},
]


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_elevations():
    locs = "|".join(f"{t['lat']},{t['lng']}" for t in TOWNS)
    url = "https://api.open-elevation.com/api/v1/lookup?locations=" + urllib.parse.quote(locs, safe="|,")
    data = http_get_json(url)
    (RAW_DIR / "elevations.json").write_text(json.dumps(data, indent=2))
    return {t["id"]: r["elevation"] for t, r in zip(TOWNS, data["results"])}


def fetch_osrm_route(from_town, to_town):
    coords = f"{from_town['lng']},{from_town['lat']};{to_town['lng']},{to_town['lat']}"
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}?geometries=geojson&overview=full&steps=false"
    data = http_get_json(url)
    (RAW_DIR / f"osrm_{from_town['id']}_{to_town['id']}.json").write_text(json.dumps(data, indent=2))
    route = data["routes"][0]
    return {
        "geometry_lonlat": route["geometry"]["coordinates"],
        "distance_m": route["distance"],
        "duration_s": route["duration"],
    }


def main():
    print("Fetching elevations (Open-Elevation)...")
    elevations = fetch_elevations()
    print(elevations)

    routes = {}
    for a, b in zip(TOWNS, TOWNS[1:]):
        key = f"{a['id']}__{b['id']}"
        print(f"Fetching OSRM route {a['name']} -> {b['name']} ...")
        routes[key] = fetch_osrm_route(a, b)
        print(f"  distance_km={routes[key]['distance_m']/1000:.1f}  duration_min={routes[key]['duration_s']/60:.1f}")
        time.sleep(1.0)  # be polite to the free public OSRM demo server

    out = {"towns": TOWNS, "elevations": elevations, "routes": routes}
    (CACHE_DIR / "corridor_data.json").write_text(json.dumps(out, indent=2))
    print(f"Wrote {CACHE_DIR / 'corridor_data.json'}")


if __name__ == "__main__":
    main()
