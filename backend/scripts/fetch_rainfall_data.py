"""
Part 10: downloads real IMD gridded daily rainfall data and extracts a small,
committable corridor subset for the Guwahati-Tawang demo.

--- SOURCE (real data) ---

Dataset: "IMD New High Spatial Resolution (0.25 x 0.25 degree) Long Period
(1901-2024) Daily Gridded Rainfall Data Set Over India" (Pai et al. 2014,
MAUSAM 65,1, pp1-18 -- cite this paper if this data is reused elsewhere).
Publisher: India Meteorological Department, Climate Prediction Group, Pune.
Official page: https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html
Format: classic NetCDF-3 (readable with scipy.io.netcdf_file -- no netCDF4/
HDF5 C library needed). One file per year.
Grid: 135 x 129 points, 0.25 x 0.25 degree, lon 66.5-100.0E, lat 6.5-38.5N.
Units: millimeter (mm). Missing/no-data cells use the sentinel -999.0 --
this script NEVER converts that to 0.0 (see rainfall_loader.py).

--- HOW THE FILE IS ACTUALLY FETCHED ---

The page itself has no direct download link -- it POSTs a year to
RF25.php, which streams back `ind{year}_rfp25.nc` as
`Content-Type: application/octet-stream`. This was verified directly
against the live IMD server (a real ~25MB NetCDF-3 file, confirmed by its
"CDF\x01" magic bytes and internal FERRET-generated header) -- not
scraped from a third-party mirror, not fabricated.

--- WHY THE RAW FILE ISN'T COMMITTED ---

A full-year raw file is ~25MB covering the whole subcontinent -- almost
none of it relevant to this one corridor. Unlike the DEM tiles (Part 4.8,
committed directly under dem_cache/), Part 10 was explicitly asked to keep
the repository small: the raw .nc is cached locally under
app/data/rainfall_cache/ (gitignored, re-downloadable from the command
below) and only the small corridor-bbox extraction below is committed, as
app/data/rainfall_corridor_<year>.csv.

--- USAGE ---

    cd backend
    python -m scripts.fetch_rainfall_data --year 2023

Re-running is idempotent: an already-cached raw .nc is reused, and the
extracted CSV is simply overwritten with the same real numbers.
"""
import argparse
import csv
import datetime
import urllib.request
import urllib.parse
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "app" / "data" / "rainfall_cache"
CORRIDOR_DATA_DIR = Path(__file__).parent.parent / "app" / "data"
DOWNLOAD_URL = "https://www.imdpune.gov.in/cmpg/Griddata/RF25.php"
DOWNLOAD_TIMEOUT_S = 120

# Corridor bounding box (backend/app/data/README.md: lat 26.01-27.75,
# lon 91.54-92.98) plus one grid cell of margin (0.25 deg) on every side, so
# nearest-grid-cell lookups near the edge of the real bbox (e.g. Guwahati,
# Tawang) always have real neighbouring cells loaded, not just the exact
# interior ones. This is deliberately still a small regional slice, not the
# whole IMD grid -- see rainfall_loader.py's module docstring for what that
# means for reuse on a different NER corridor.
CORRIDOR_LAT_MIN, CORRIDOR_LAT_MAX = 25.75, 28.00
CORRIDOR_LON_MIN, CORRIDOR_LON_MAX = 91.50, 93.00

IMD_MISSING_VALUE = -999.0


def _raw_cache_path(year: int) -> Path:
    return CACHE_DIR / f"ind{year}_rfp25.nc"


def download_year(year: int, force: bool = False) -> Path:
    """Downloads one year's real IMD gridded-rainfall NetCDF file via the
    exact POST the public form on Rainfall_25_NetCDF.html submits, and
    caches it locally. Never fabricates a file -- raises on any download
    failure rather than silently proceeding without real data."""
    dest = _raw_cache_path(year)
    if dest.exists() and not force:
        return dest

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = urllib.parse.urlencode({"RF25": str(year)}).encode("ascii")
    request = urllib.request.Request(
        DOWNLOAD_URL, data=data, headers={"User-Agent": "Mozilla/5.0"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        payload = resp.read()

    if payload[:3] != b"CDF":
        raise ValueError(
            f"IMD server did not return a NetCDF file for year {year} "
            f"(got {len(payload)} bytes starting {payload[:16]!r}) -- the year may "
            "not be published yet, or the server response format changed."
        )

    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)
    return dest


def extract_corridor_subset(nc_path: Path, year: int) -> Path:
    """Reads the real NetCDF file (scipy.io.netcdf_file -- classic NetCDF-3,
    no HDF5/netCDF4 C library dependency) and writes a small CSV covering
    only the corridor bounding box, every day of the year, one row per
    (date, grid_lat, grid_lon). A cell holding IMD's -999.0 sentinel is
    written with an EMPTY rainfall_mm field -- never 0.0 -- so a missing
    observation can never be silently read back as "no rain"."""
    from scipy.io import netcdf_file

    f = netcdf_file(nc_path, "r", mmap=False)
    lon = f.variables["LONGITUDE"][:].copy()
    lat = f.variables["LATITUDE"][:].copy()
    time = f.variables["TIME"][:].copy()  # days since 1900-12-31
    rainfall = f.variables["RAINFALL"]

    lat_idx = [i for i, v in enumerate(lat) if CORRIDOR_LAT_MIN <= v <= CORRIDOR_LAT_MAX]
    lon_idx = [j for j, v in enumerate(lon) if CORRIDOR_LON_MIN <= v <= CORRIDOR_LON_MAX]
    if not lat_idx or not lon_idx:
        raise ValueError(f"Corridor bounding box did not match any grid cell in {nc_path}")

    epoch = datetime.date(1900, 12, 31)
    out_path = CORRIDOR_DATA_DIR / f"rainfall_corridor_{year}.csv"

    rows_written = 0
    with out_path.open("w", newline="") as out_f:
        writer = csv.writer(out_f)
        writer.writerow(["date", "lat", "lon", "rainfall_mm"])
        for t_idx, t_val in enumerate(time):
            obs_date = epoch + datetime.timedelta(days=int(t_val))
            day_grid = rainfall[t_idx]  # (LATITUDE, LONGITUDE), real values incl. -999.0 sentinel
            for i in lat_idx:
                for j in lon_idx:
                    value = float(day_grid[i, j])
                    rainfall_mm = "" if value <= IMD_MISSING_VALUE + 1e-6 else f"{value:.2f}"
                    writer.writerow([obs_date.isoformat(), f"{lat[i]:.2f}", f"{lon[j]:.2f}", rainfall_mm])
                    rows_written += 1

    print(f"Wrote {out_path} ({rows_written} rows, {len(lat_idx)}x{len(lon_idx)} grid cells x {len(time)} days)")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2023, help="IMD data year to fetch (default: 2023)")
    parser.add_argument("--force-download", action="store_true", help="Re-download even if cached")
    args = parser.parse_args()

    print(f"Fetching real IMD gridded rainfall for {args.year}...")
    nc_path = download_year(args.year, force=args.force_download)
    print(f"Raw file cached at {nc_path} ({nc_path.stat().st_size:,} bytes)")
    extract_corridor_subset(nc_path, args.year)


if __name__ == "__main__":
    main()
