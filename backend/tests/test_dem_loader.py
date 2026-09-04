"""
Unit tests for dem_loader.py's tile addressing and point-sampling math,
using small synthetic arrays instead of the real 3601x3601 SRTM tiles —
fast, deterministic, and doesn't touch the network or the on-disk cache.
"""
import numpy as np
import pytest

from app.data.dem_loader import VOID_VALUE, DemLoader, DemTile, _tile_name


def test_tile_name_positive_lat_lon():
    assert _tile_name(26, 91) == "N26E091"


def test_tile_name_negative_lat_lon():
    assert _tile_name(-1, -5) == "S01W005"


def test_tile_name_zero():
    assert _tile_name(0, 0) == "N00E000"


def _small_tile(south=26, west=91, size=4):
    """A tiny synthetic tile: elevation increases by 10 per row (northward)
    and by 1 per column (eastward), for easy manual verification."""
    array = np.zeros((size, size), dtype=np.int16)
    for row in range(size):
        for col in range(size):
            array[row, col] = (size - 1 - row) * 10 + col
    return DemTile(south=south, west=west, array=array)


def test_elevation_at_exact_grid_point_matches_array_value():
    tile = _small_tile()
    # array[0,0] is the NW corner = (south+1, west)
    assert tile.elevation_at(27.0, 91.0) == 30.0
    # array[-1,-1] is the SE corner = (south, west+1)
    assert tile.elevation_at(26.0, 92.0) == 3.0


def test_elevation_at_bilinear_interpolates_between_grid_points():
    tile = _small_tile()
    # Midpoint between array[0,0]=30 and array[0,1]=31 (same row, half a
    # column east) should interpolate to 30.5.
    n = 3  # size-1
    lon_mid = 91.0 + 0.5 / n
    result = tile.elevation_at(27.0, lon_mid)
    assert result == pytest.approx(30.5, abs=0.01)


def test_elevation_outside_tile_bounds_returns_none():
    tile = _small_tile()
    assert tile.elevation_at(28.5, 91.5) is None  # north of this tile entirely
    assert tile.elevation_at(26.5, 89.0) is None  # west of this tile entirely


def test_void_value_returns_none_not_fabricated():
    array = np.full((4, 4), VOID_VALUE, dtype=np.int16)
    tile = DemTile(south=26, west=91, array=array)
    assert tile.elevation_at(26.5, 91.5) is None


def test_void_value_does_not_silently_average_into_a_real_looking_number():
    size = 4
    array = np.full((size, size), 500, dtype=np.int16)
    array[0, 0] = VOID_VALUE  # corrupt just one of the 4 bilinear corners
    tile = DemTile(south=26, west=91, array=array)
    # A point close to the (1,1) corner (valid=500) but still within the
    # same grid cell as the void (0,0) corner: naive bilinear interpolation
    # of [-32768, 500, 500, 500] would produce a wildly wrong negative
    # number. The void-aware path must instead fall back to the nearest
    # corner (here, the valid 500) rather than ever returning that
    # corrupted interpolated value.
    result = tile.elevation_at(26.7, 91.3)
    assert result == 500.0


def test_dem_loader_returns_none_when_download_unavailable(tmp_path):
    """With allow_download=False and an empty cache dir, missing tiles must
    return None (missing data) rather than raise or fabricate a value."""
    loader = DemLoader(cache_dir=tmp_path, allow_download=False)
    assert loader.elevation_at(0.5, 0.5) is None  # tile N00E000 not cached, download disabled


def test_dem_loader_caches_tile_in_memory(tmp_path, monkeypatch):
    import app.data.dem_loader as dem_loader_module

    call_count = {"n": 0}
    real_load = dem_loader_module._load_tile_array

    def counting_load(path):
        call_count["n"] += 1
        return real_load(path)

    # Pre-seed the cache dir with a fake tile file (skip network entirely).
    array = np.full((4, 4), 123, dtype=np.int16)
    import gzip

    tile_path = tmp_path / "N26E091.hgt.gz"
    with gzip.open(tile_path, "wb") as f:
        f.write(array.astype(">i2").tobytes())

    monkeypatch.setattr(dem_loader_module, "TILE_SAMPLES", 4)
    monkeypatch.setattr(dem_loader_module, "_load_tile_array", counting_load)

    loader = DemLoader(cache_dir=tmp_path, allow_download=False)
    loader.elevation_at(26.5, 91.5)
    loader.elevation_at(26.6, 91.6)
    assert call_count["n"] == 1  # second call must reuse the in-memory tile
