"""
The 7 demonstration locations for the Guwahati-Tawang SIH corridor
(ARCHITECTURE.md). This is deliberately the ONLY file that hardcodes this
specific corridor's towns — app/data/osm_geojson_loader.py itself is a
generic OSM-GeoJSON-to-graph converter with no knowledge of "Guwahati" or
"Tawang". A different NER corridor/dataset would define its own list like
this one and pass it to the loader, without touching the loader or
routing_engine.

Coordinates: OpenStreetMap Nominatim geocoding (Part 2).
Elevations: Open-Elevation API, SRTM-derived (Part 2).
See backend/app/data/README.md for full provenance.
"""

DEMO_LOCATIONS = [
    {"name": "Guwahati", "type": "city", "lat": 26.1805978, "lng": 91.7539430, "elevation_m": 55.0},
    {"name": "Tezpur", "type": "town", "lat": 26.6229928, "lng": 92.7976082, "elevation_m": 80.0},
    {"name": "Bhalukpong", "type": "town", "lat": 27.0137235, "lng": 92.6358068, "elevation_m": 216.0},
    {"name": "Bomdila", "type": "city", "lat": 27.2644450, "lng": 92.4206519, "elevation_m": 2399.0},
    {"name": "Dirang", "type": "town", "lat": 27.3600800, "lng": 92.2412100, "elevation_m": 1621.0},
    {"name": "Sela Pass", "type": "mountain_pass", "lat": 27.5035642, "lng": 92.1044346, "elevation_m": 4189.0},
    {"name": "Tawang", "type": "city", "lat": 27.5879186, "lng": 91.8637330, "elevation_m": 2853.0},
]
