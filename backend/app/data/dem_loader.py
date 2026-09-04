"""
Loads real DEM (Digital Elevation Model) tiles and answers point-elevation
queries. This module knows nothing about roads, segments, or geometry —
see dem_processor.py for turning a road segment's geometry into an
elevation profile / slope. That split means a different DEM source (a
different resolution, a local GeoTIFF, a paid API) can be substituted later
by writing a new loader with the same elevation_at(lat, lon) interface,
without touching dem_processor.py or network_loader.py.

--- DEM SOURCE (real data — see backend/app/data/README.md for the summary
    written for this dataset) ---

Dataset: "Terrain Tiles" — AWS Registry of Open Data
  (https://registry.opendata.aws/terrain-tiles/), "skadi" layout.
Underlying data: SRTM (NASA Shuttle Radar Topography Mission) 1-arc-second
  global elevation, void-filled using auxiliary sources by the tile-building
  pipeline (Tilezen/Joerd) that produces this bucket.
Format: standard NASA/USGS ".hgt" SRTM1 tiles — one file per 1deg x 1deg
  cell, 3601 x 3601 samples, big-endian signed 16-bit integers, elevation in
  whole metres, gzip-compressed on the server (`.hgt.gz`).
Resolution: 1 arc-second (~30m at this latitude).
CRS: WGS84 geographic coordinates (EPSG:4326); elevation values are heights
  above the EGM96 geoid (the standard SRTM vertical datum) — not ellipsoidal
  height.
Access: public, unauthenticated HTTPS, no API key —
  https://s3.amazonaws.com/elevation-tiles-prod/skadi/{NxxExxx[:3]}/{tile}.hgt.gz
Void handling: cells with no data are encoded as -32768 in the raw grid
  (the standard SRTM void sentinel). This loader treats that value as
  missing — see DemTile.elevation_at — it is never treated as sea level or
  interpolated across silently.

--- What this module downloads for THIS corridor ---

The Guwahati-Tawang corridor's bounding box (lat 26.01-27.75, lon
91.54-92.98) is covered by exactly 4 one-degree tiles: N26E091, N26E092,
N27E091, N27E092. These were downloaded once (see backend/app/data/README.md
"DEM provenance" section for the exact commands used) and are cached under
dem_cache/ as the original .hgt.gz files — loaded from local disk at
runtime, not re-downloaded on every run, the same reasoning this project
already applies to the supplied OSM GeoJSON extract (see
osm_geojson_loader.py's module docstring: re-fetching from a remote source
on every run is slow/unreliable; a real file fetched once and committed to
the cache is not).

If a tile is missing from the cache, get_default_dem_loader() will attempt
to download it on first use (same URL scheme as above); if that also fails
(no network), DemLoader.elevation_at() returns None for points in that
tile rather than raising or fabricating a value — callers (dem_processor.py)
must treat None as "no real measurement available" and are required to
say so in RoadSegment.source, never silently substitute a placeholder
labelled as real.
"""
import gzip
import math
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

TILE_SAMPLES = 3601  # SRTM1: 3601x3601 samples covering exactly 1deg x 1deg
VOID_VALUE = -32768  # standard SRTM "no data" sentinel
CACHE_DIR = Path(__file__).parent / "dem_cache"
BASE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
DOWNLOAD_TIMEOUT_S = 30

DEM_SOURCE_NAME = "AWS Terrain Tiles (Skadi/SRTM1, ~30m, void-filled)"


def _tile_name(lat_floor: int, lon_floor: int) -> str:
    lat_hem = "N" if lat_floor >= 0 else "S"
    lon_hem = "E" if lon_floor >= 0 else "W"
    return f"{lat_hem}{abs(lat_floor):02d}{lon_hem}{abs(lon_floor):03d}"


def _tile_url(tile_name: str) -> str:
    return f"{BASE_URL}/{tile_name[:3]}/{tile_name}.hgt.gz"


def _download_tile(tile_name: str, dest_path: Path) -> bool:
    """Best-effort download; returns False (never raises) on any network
    failure so callers can fall back cleanly instead of crashing the whole
    pipeline over one missing tile."""
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(_tile_url(tile_name), timeout=DOWNLOAD_TIMEOUT_S) as resp:
            data = resp.read()
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        tmp_path.write_bytes(data)
        tmp_path.replace(dest_path)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _load_tile_array(path: Path):
    import numpy as np

    with gzip.open(path, "rb") as f:
        raw = f.read()
    expected_bytes = TILE_SAMPLES * TILE_SAMPLES * 2
    if len(raw) != expected_bytes:
        raise ValueError(
            f"{path}: expected a {expected_bytes}-byte SRTM1 tile, got {len(raw)} bytes "
            "(corrupt download or unexpected DEM format)"
        )
    arr = np.frombuffer(raw, dtype=">i2").reshape(TILE_SAMPLES, TILE_SAMPLES)
    return arr


class DemTile:
    """One 1deg x 1deg SRTM1 tile. Grid is stored north-to-south,
    west-to-east: array[0, 0] is the tile's NW corner (south+1, west);
    array[-1, -1] is the SE corner (south, west+1)."""

    def __init__(self, south: int, west: int, array):
        self.south = south
        self.west = west
        self.array = array

    def elevation_at(self, lat: float, lon: float) -> Optional[float]:
        """Bilinear interpolation between the 4 surrounding grid samples.
        Returns None if any required sample is a SRTM void (-32768) or the
        point falls outside this tile — never fabricates a value to fill
        the gap."""
        n = self.array.shape[0] - 1  # intervals per degree (3600 for a real SRTM1 tile)
        row_f = (self.south + 1 - lat) * n
        col_f = (lon - self.west) * n
        if not (0.0 <= row_f <= n and 0.0 <= col_f <= n):
            return None

        r0 = min(int(math.floor(row_f)), n - 1)
        c0 = min(int(math.floor(col_f)), n - 1)
        r1, c1 = r0 + 1, c0 + 1
        fr, fc = row_f - r0, col_f - c0

        corners = [
            self.array[r0, c0],
            self.array[r0, c1],
            self.array[r1, c0],
            self.array[r1, c1],
        ]
        if any(v == VOID_VALUE for v in corners):
            # Don't interpolate across a void — fall back to the single
            # nearest corner if it happens to be valid, else give up.
            nearest = self.array[round(row_f), round(col_f)]
            return float(nearest) if nearest != VOID_VALUE else None

        top = corners[0] * (1 - fc) + corners[1] * fc
        bottom = corners[2] * (1 - fc) + corners[3] * fc
        return float(top * (1 - fr) + bottom * fr)


class DemLoader:
    """Caches parsed tiles in memory (per process) and on disk (across
    runs) under `cache_dir`. Construct one instance and reuse it across all
    segments — re-parsing a 3601x3601 tile per point query would be needlessly
    slow."""

    def __init__(self, cache_dir: Path = CACHE_DIR, allow_download: bool = True):
        self.cache_dir = cache_dir
        self.allow_download = allow_download
        self._tiles: dict[str, Optional[DemTile]] = {}

    def _get_tile(self, lat_floor: int, lon_floor: int) -> Optional[DemTile]:
        name = _tile_name(lat_floor, lon_floor)
        if name in self._tiles:
            return self._tiles[name]

        path = self.cache_dir / f"{name}.hgt.gz"
        if not path.exists():
            if not self.allow_download or not _download_tile(name, path):
                self._tiles[name] = None
                return None

        try:
            array = _load_tile_array(path)
        except (ValueError, OSError):
            self._tiles[name] = None
            return None

        tile = DemTile(south=lat_floor, west=lon_floor, array=array)
        self._tiles[name] = tile
        return tile

    def elevation_at(self, lat: float, lon: float) -> Optional[float]:
        tile = self._get_tile(math.floor(lat), math.floor(lon))
        if tile is None:
            return None
        return tile.elevation_at(lat, lon)


_default_loader: Optional[DemLoader] = None


def get_default_dem_loader() -> DemLoader:
    """Process-wide singleton so the corridor's tiles are parsed once, not
    once per road segment."""
    global _default_loader
    if _default_loader is None:
        _default_loader = DemLoader()
    return _default_loader
