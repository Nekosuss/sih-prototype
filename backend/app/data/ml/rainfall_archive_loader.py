"""
Part 14: reads the full 11-year real IMD gridded-rainfall archive
(2015-2025, one NetCDF-3 file per year) and produces per-(grid cell, year)
aggregates for segment-year feature construction.

--- DATA SOURCE (real data) ---

Same IMD 0.25x0.25 degree daily gridded rainfall product already used by
backend/scripts/fetch_rainfall_data.py (Pai et al. 2014) -- same schema,
same missing-value sentinel (-999.0), same variable names
(LONGITUDE/LATITUDE/TIME/RAINFALL), same "days since 1900-12-31" time
units. That script extracts and commits only a single year's small
corridor-bbox CSV; this module instead reads the FULL 11-year archive
directly from the repository-root `data/` directory (not committed to git
-- 11 files x ~25MB, deliberately kept out of version control, same
reasoning as backend/app/data/rainfall_cache/ being gitignored) to build
year-level rainfall aggregates for every year the archive covers.

This module is offline/build-time only -- nothing in app/core, app/api, or
app/simulation imports it. It does not touch app/core/weather_factor.py or
the live single-year rainfall_loader.py used by the running API.

--- Verified schema (see app/data/ml_dataset_inspection_part14.md) ---

Every year: variables LONGITUDE(135), LATITUDE(129), TIME, RAINFALL
(TIME, LATITUDE, LONGITUDE); full-India grid lon 66.5-100.0E, lat
6.5-38.5N; missing sentinel -999.0; zero date gaps within a year. The
corridor bbox subset (same as fetch_rainfall_data.py) is 10x7 = 70 grid
cells; 3 of those 70 are permanently -999 every day of every year checked
(the Bhutan-border corner) -- confirmed in the inspection doc that no real
road segment's nearest cell falls on one of those 3.
"""
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_YEAR_RE = re.compile(r"ind(\d{4})_rfp25")

REPO_ROOT = Path(__file__).resolve().parents[4]
RAINFALL_ARCHIVE_DIR = REPO_ROOT / "data"

# Same corridor bbox as fetch_rainfall_data.py -- kept identical so the two
# never disagree about "which cells count as corridor cells."
CORRIDOR_LAT_MIN, CORRIDOR_LAT_MAX = 25.75, 28.00
CORRIDOR_LON_MIN, CORRIDOR_LON_MAX = 91.50, 93.00

IMD_MISSING_VALUE = -999.0
RAINY_DAY_THRESHOLD_MM = 1.0  # a common operational "rain day" cutoff; informational feature only
MONSOON_START_MONTH, MONSOON_END_MONTH = 6, 9  # Jun-Sep, standard Indian monsoon window

_EPOCH = datetime.date(1900, 12, 31)


@dataclass(frozen=True)
class CellYearRainfall:
    """One grid cell's real rainfall aggregate for one calendar year.
    `missing_days` counts days where this cell held IMD's own -999.0
    sentinel -- never silently treated as 0mm (mirrors rainfall_loader.py's
    existing missing-value handling)."""

    grid_lat: float
    grid_lon: float
    year: int
    annual_rainfall_mm: Optional[float]
    monsoon_jun_sep_rainfall_mm: Optional[float]
    max_daily_rainfall_mm: Optional[float]
    rainy_days_count: Optional[int]
    missing_days: int
    total_days: int


def available_years() -> list[int]:
    """Years actually present in the archive directory -- never hardcoded,
    always reflects what's really on disk."""
    years = []
    for path in sorted(RAINFALL_ARCHIVE_DIR.glob("RF25_ind*_rfp25.nc")):
        m = _YEAR_RE.search(path.stem)
        if m:
            years.append(int(m.group(1)))
    return sorted(years)


def load_year_corridor_cells(year: int) -> dict[tuple[float, float], CellYearRainfall]:
    """
    Reads one year's real NetCDF file and returns, for every grid cell in
    the corridor bbox, a CellYearRainfall aggregate keyed by
    (grid_lat, grid_lon) -- rounded to match the file's own 0.25-degree
    coordinate values exactly, no re-gridding or interpolation.
    """
    from scipy.io import netcdf_file

    path = RAINFALL_ARCHIVE_DIR / f"RF25_ind{year}_rfp25.nc"
    if not path.exists():
        raise FileNotFoundError(
            f"Rainfall archive file not found: {path}. Expected the real IMD "
            f"NetCDF files under {RAINFALL_ARCHIVE_DIR} (see module docstring)."
        )

    f = netcdf_file(path, "r", mmap=False)
    try:
        lon = f.variables["LONGITUDE"][:].copy()
        lat = f.variables["LATITUDE"][:].copy()
        time = f.variables["TIME"][:].copy()
        rainfall = f.variables["RAINFALL"][:].copy()  # (time, lat, lon)

        lat_idx = [i for i, v in enumerate(lat) if CORRIDOR_LAT_MIN <= v <= CORRIDOR_LAT_MAX]
        lon_idx = [j for j, v in enumerate(lon) if CORRIDOR_LON_MIN <= v <= CORRIDOR_LON_MAX]

        monsoon_day_mask = []
        for t_val in time:
            d = _EPOCH + datetime.timedelta(days=int(t_val))
            monsoon_day_mask.append(MONSOON_START_MONTH <= d.month <= MONSOON_END_MONTH)

        cells: dict[tuple[float, float], CellYearRainfall] = {}
        for i in lat_idx:
            for j in lon_idx:
                series = rainfall[:, i, j]
                valid_mask = series > (IMD_MISSING_VALUE + 1e-6)
                missing_days = int((~valid_mask).sum())
                total_days = len(series)

                valid_values = series[valid_mask]
                if valid_values.size == 0:
                    cells[(round(float(lat[i]), 2), round(float(lon[j]), 2))] = CellYearRainfall(
                        grid_lat=round(float(lat[i]), 2), grid_lon=round(float(lon[j]), 2), year=year,
                        annual_rainfall_mm=None, monsoon_jun_sep_rainfall_mm=None,
                        max_daily_rainfall_mm=None, rainy_days_count=None,
                        missing_days=missing_days, total_days=total_days,
                    )
                    continue

                monsoon_values = [
                    v for v, is_monsoon, is_valid in zip(series, monsoon_day_mask, valid_mask)
                    if is_monsoon and is_valid
                ]

                cells[(round(float(lat[i]), 2), round(float(lon[j]), 2))] = CellYearRainfall(
                    grid_lat=round(float(lat[i]), 2),
                    grid_lon=round(float(lon[j]), 2),
                    year=year,
                    annual_rainfall_mm=round(float(valid_values.sum()), 2),
                    monsoon_jun_sep_rainfall_mm=round(float(sum(monsoon_values)), 2) if monsoon_values else None,
                    max_daily_rainfall_mm=round(float(valid_values.max()), 2),
                    rainy_days_count=int((valid_values > RAINY_DAY_THRESHOLD_MM).sum()),
                    missing_days=missing_days,
                    total_days=total_days,
                )
        return cells
    finally:
        f.close()


def nearest_cell(lat: float, lon: float, cells: dict[tuple[float, float], CellYearRainfall]) -> tuple[float, float]:
    """Nearest real grid-cell center to (lat, lon) among the cells actually
    loaded for this year -- plain nearest-neighbor over real coordinates,
    no interpolation/fabrication between cells."""
    return min(cells.keys(), key=lambda cell: (cell[0] - lat) ** 2 + (cell[1] - lon) ** 2)
