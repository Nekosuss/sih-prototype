"""
Loads the real IMD gridded-rainfall corridor extraction (see
backend/scripts/fetch_rainfall_data.py for provenance and how
app/data/rainfall_corridor_<year>.csv was produced) and answers
point/date rainfall queries. This module knows nothing about roads,
segments, or risk scoring -- see app/core/weather_factor.py for turning a
rainfall observation into a weather_factor, and
app/data/rainfall_validation.py for the segment/named-location report. This
split mirrors dem_loader.py/dem_processor.py's separation (Part 4.8): a
different rainfall source (a different year, a different region extract, a
live IMD feed) can be substituted later by writing a new loader with the
same get_daily_rainfall(lat, lon, date) interface.

--- DATA SOURCE (real data) ---

IMD New High Spatial Resolution (0.25 x 0.25 degree) Long Period Daily
Gridded Rainfall Data Set Over India (Pai et al. 2014). Downloaded directly
from https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html's
underlying RF25.php endpoint -- a real ~25MB NetCDF-3 file per year, parsed
with scipy.io.netcdf_file (no netCDF4/HDF5 dependency needed for the
classic NetCDF-3 format IMD publishes). See fetch_rainfall_data.py's module
docstring for the exact download mechanism and why only a small corridor
subset is committed to this repository rather than the full-India file.

Grid: 0.25 x 0.25 degree, units millimetre/day. IMD's own missing-value
sentinel is -999.0 -- see IMD_MISSING_VALUE below. This loader NEVER
converts that (or "no cached data for this coordinate/date") into 0.0;
every such case is reported as RainfallStatus.missing_value or
RainfallStatus.no_coverage, and rainfall_mm is None in both cases. Only a
genuine real observed value (including a genuine real 0.0 -- a day it
really didn't rain) gets RainfallStatus.ok.

--- WHAT "COVERAGE" MEANS HERE ---

The committed CSV is a small extraction: only the Guwahati-Tawang corridor
bounding box (+ a small margin), and only the year(s) actually extracted
(2023 by default -- see DEFAULT_RAINFALL_OBSERVATION_DATE, app/config.py).
A coordinate or date outside that extracted subset is genuinely
NOT LOADED here -- reported as `no_coverage`, not silently treated as "no
rain" or extrapolated. Extending this to a different NER corridor or a
different year means re-running fetch_rainfall_data.py with a different
bounding box/year and pointing DEFAULT_CORRIDOR_CSV (or an explicit
csv_path) at the new file -- no code in this module hardcodes "Guwahati" or
"Tawang".
"""
import csv
import datetime
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent
DEFAULT_CORRIDOR_CSV = DATA_DIR / "rainfall_corridor_2023.csv"

# IMD's own "no data" sentinel for this dataset (see fetch_rainfall_data.py).
# Already resolved to an empty CSV field by the extraction script; kept here
# too since a loader is exactly the layer that should know what a raw IMD
# value like this would mean, should a caller ever hand it a raw grid value.
IMD_MISSING_VALUE = -999.0

# The real IMD grid spacing is exactly 0.25 degree -- a query point can be at
# most half that away from its nearest real grid centre (diagonal:
# sqrt(0.125^2 + 0.125^2) =~ 0.177 deg). LOOKUP_MAX_DISTANCE_DEG is set a
# little above that so every point genuinely inside the loaded grid resolves,
# while a point far outside this corridor extraction's small bounding box
# (e.g. a coordinate in a different part of India, or a different country)
# correctly reports `no_coverage` rather than silently snapping to a
# many-degrees-away "nearest" cell that says nothing about local rainfall.
LOOKUP_MAX_DISTANCE_DEG = 0.2


class RainfallStatus(str, Enum):
    ok = "ok"  # a real observed value -- may legitimately be 0.0
    missing_value = "missing_value"  # a real IMD grid cell exists here, but its value for this date is the -999 no-data sentinel
    no_coverage = "no_coverage"  # this coordinate or date falls outside the locally extracted dataset entirely


@dataclass(frozen=True)
class RainfallObservation:
    requested_lat: float
    requested_lon: float
    date: datetime.date
    grid_lat: Optional[float]
    grid_lon: Optional[float]
    rainfall_mm: Optional[float]
    status: RainfallStatus
    source: str
    grid_resolution_deg: float = 0.25


SOURCE_NAME = (
    "IMD gridded rainfall 0.25x0.25 deg (Pai et al. 2014), corridor extraction "
    "-- see backend/scripts/fetch_rainfall_data.py"
)


def _parse_date(value) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(value)


class RainfallLoader:
    """Loads one corridor-extraction CSV into memory once and answers
    nearest-grid-cell point/date queries. Construct one instance and reuse
    it (see get_default_rainfall_loader()) rather than re-parsing the CSV
    per query."""

    def __init__(self, csv_path: Path = DEFAULT_CORRIDOR_CSV):
        self.csv_path = csv_path
        # (date, grid_lat, grid_lon) -> rainfall_mm (float) or None (missing_value sentinel)
        self._observations: dict[tuple[datetime.date, float, float], Optional[float]] = {}
        self._grid_lats: set[float] = set()
        self._grid_lons: set[float] = set()
        self._dates: set[datetime.date] = set()
        self._load()

    def _load(self) -> None:
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"Rainfall corridor extraction not found: {self.csv_path}. Run "
                "`cd backend && python -m scripts.fetch_rainfall_data` first."
            )
        with self.csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                obs_date = _parse_date(row["date"])
                grid_lat = round(float(row["lat"]), 2)
                grid_lon = round(float(row["lon"]), 2)
                raw = row["rainfall_mm"].strip()
                rainfall_mm = None if raw == "" else float(raw)
                self._observations[(obs_date, grid_lat, grid_lon)] = rainfall_mm
                self._grid_lats.add(grid_lat)
                self._grid_lons.add(grid_lon)
                self._dates.add(obs_date)

        if not self._observations:
            raise ValueError(f"{self.csv_path} loaded but contains no rows")

    @property
    def grid_cell_count(self) -> int:
        return len(self._grid_lats) * len(self._grid_lons)

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    @property
    def date_range(self) -> tuple[datetime.date, datetime.date]:
        return min(self._dates), max(self._dates)

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """(lat_min, lat_max, lon_min, lon_max) of the loaded grid."""
        return min(self._grid_lats), max(self._grid_lats), min(self._grid_lons), max(self._grid_lons)

    @property
    def dates(self) -> list[datetime.date]:
        return sorted(self._dates)

    @property
    def missing_value_count(self) -> int:
        """Number of loaded (date, grid cell) observations that are IMD's
        real -999.0 no-data sentinel -- never coerced to 0.0 (see _load())."""
        return sum(1 for v in self._observations.values() if v is None)

    def daily_max_mm(self, date) -> Optional[float]:
        """The maximum real (non-missing) rainfall_mm across every loaded
        grid cell for one date, or None if every cell that date is missing.
        Used for reporting (app/data/rainfall_validation.py) -- never used
        by get_daily_rainfall(), which always answers for one specific
        point, not "the worst cell somewhere in the loaded box.\""""
        obs_date = _parse_date(date)
        values = [v for (d, _lat, _lon), v in self._observations.items() if d == obs_date and v is not None]
        return max(values) if values else None

    def _nearest_grid_cell(self, lat: float, lon: float) -> Optional[tuple[float, float]]:
        """Nearest loaded grid cell by simple Euclidean degree distance (the
        grid is a regular lat/lon lattice over a small area, so this agrees
        with a true geodesic nearest-cell result here). Returns None if
        every loaded cell is farther than LOOKUP_MAX_DISTANCE_DEG -- i.e.
        this coordinate has no real coverage in this extraction."""
        best_cell = None
        best_dist = None
        for grid_lat in self._grid_lats:
            for grid_lon in self._grid_lons:
                dist = math.hypot(lat - grid_lat, lon - grid_lon)
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_cell = (grid_lat, grid_lon)
        if best_cell is None or best_dist > LOOKUP_MAX_DISTANCE_DEG:
            return None
        return best_cell

    def get_daily_rainfall(self, lat: float, lon: float, date) -> RainfallObservation:
        """The loader's single entry point. `date` may be a datetime.date or
        an ISO 'YYYY-MM-DD' string. Never raises for an out-of-coverage
        coordinate/date -- reports RainfallStatus.no_coverage instead, since
        "this demo corridor extraction doesn't include that point/day" is an
        expected, not exceptional, situation for callers to handle."""
        obs_date = _parse_date(date)

        if obs_date not in self._dates:
            return RainfallObservation(
                requested_lat=lat, requested_lon=lon, date=obs_date,
                grid_lat=None, grid_lon=None, rainfall_mm=None,
                status=RainfallStatus.no_coverage, source=SOURCE_NAME,
            )

        cell = self._nearest_grid_cell(lat, lon)
        if cell is None:
            return RainfallObservation(
                requested_lat=lat, requested_lon=lon, date=obs_date,
                grid_lat=None, grid_lon=None, rainfall_mm=None,
                status=RainfallStatus.no_coverage, source=SOURCE_NAME,
            )

        grid_lat, grid_lon = cell
        rainfall_mm = self._observations.get((obs_date, grid_lat, grid_lon))
        status = RainfallStatus.missing_value if rainfall_mm is None else RainfallStatus.ok
        return RainfallObservation(
            requested_lat=lat, requested_lon=lon, date=obs_date,
            grid_lat=grid_lat, grid_lon=grid_lon, rainfall_mm=rainfall_mm,
            status=status, source=SOURCE_NAME,
        )


_default_loader: Optional[RainfallLoader] = None


def get_default_rainfall_loader() -> RainfallLoader:
    """Process-wide singleton so the corridor CSV is parsed once, not once
    per segment/request (mirrors dem_loader.get_default_dem_loader())."""
    global _default_loader
    if _default_loader is None:
        _default_loader = RainfallLoader()
    return _default_loader
