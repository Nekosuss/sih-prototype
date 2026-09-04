"""
Part 11: generic spatial hazard-ZONATION layer loader/interface, for
LANDSLIDE SUSCEPTIBILITY and FLOOD HAZARD -- explicitly DIFFERENT concepts
from Part 4's GSI HISTORICAL landslide inventory
(app/data/landslide_mapper.py, RoadSegment.historical_landslide_count):

    historical inventory        -> "where have landslides been OBSERVED"
    landslide hazard zonation   -> "which areas are MORE PRONE to one"
    flood hazard zonation       -> "which areas are exposed to flooding"

Both are kept as separate RoadSegment fields (see app/models/network.py)
and separate loaders/pipelines -- this module never reads or writes
historical_landslide_count/nearest_landslide_distance_m, and
landslide_mapper.py never reads or writes anything in this module.

--- PRIMARY OFFICIAL SOURCE ---

Arunachal Pradesh State Remote Sensing Application Centre (APSAC/SRSAC):
  https://www.srsac.arunachal.gov.in/admin/geospatial.html
  https://www.srsac.arunachal.gov.in/geospatial.php  (mirror)

The catalogue lists, among other thematic layers:
  - Landslide Hazard Zonation Map, 1:50K (state-wide), AND a large-scale
    1:10K zonation specifically for Tawang / West Kameng / East Kameng /
    Pakke-Kessang / Papumpare -- i.e. exactly this project's corridor
    districts.
  - Flood Hazard Zonation Map, 1:25K, state-wide.
  - Slope Maps and Road Network layers, 1:50K.

--- VERIFIED DATA-ACCESS STATUS (checked directly against both live pages
    at Part 11 implementation time, 2026) ---

Neither page offers a direct download link, download button, or
machine-readable file URL. Both instruct the user to submit a manual data
request (an email address / "Submit Data Request" contact-form workflow)
rather than self-service download -- there is no API, no bulk download, no
public GeoServer/WMS endpoint advertised. The 1:10K landslide zonation
covering this project's own corridor districts was itself listed on the
catalogue page as a still-in-progress database ("Database will be ready by
June, 2024") at the time this was checked.

**Consequence: no official APSAC hazard-zonation file has been downloaded,
and none is bundled with this repository.** Per this project's data
integrity rules, that means:

  - This module's spatial-lookup mechanics (point-in-polygon, source-class
    -> normalized-score mapping, missing/no-coverage handling) are real,
    working code, verified against tiny SYNTHETIC polygons in
    tests/test_hazard_layer.py.
  - Those synthetic polygons are ONLY unit-test fixtures. They are never
    read by the running application (get_default_hazard_layer_loader()
    only ever looks at backend/app/data/hazard_layers/, see below) and must
    never be presented as real Arunachal Pradesh landslide/flood data.
  - Every landslide_hazard_*/flood_hazard_* value the running application
    currently reports is `no_coverage`/`None` -- an honest reflection of
    "no official production layer is locally available", not a fabricated
    "low hazard" reading.

--- WHERE A REAL FILE GOES, ONCE OBTAINED ---

See backend/app/data/hazard_layers/README.md for the full drop-in
instructions. In short: place a GeoJSON/Shapefile/GeoPackage (any CRS --
reprojected to EPSG:4326 on load) at
`backend/app/data/hazard_layers/landslide_hazard_zonation.<ext>` and/or
`.../flood_hazard_zonation.<ext>`, with a polygon attribute column (default
name `hazard_class`) whose values are covered by
app/config.py::HAZARD_CLASS_TO_SCORE, then re-run
`python -m app.data.hazard_layer_mapper`. No code in this module, or in any
of its callers, needs to change.

--- RASTER SUPPORT ---

The Part 11 spec allows for a raster hazard layer "if the official source
provides raster data". Since the official source could not actually be
accessed, its real format (vector zonation polygons vs. a classified raster
grid) is unknown -- building raster support now would be guessing at an
interface for a file this project has never seen, and would add a new
heavy geospatial dependency (rasterio) purely speculatively. Only the
polygon/vector path is implemented; HazardLayerLoader's public
get_landslide_hazard()/get_flood_hazard() interface is format-agnostic at
the call site, so a raster-backed layer could be added later (e.g. a
RasterHazardLayer with the same .query(lat, lon) -> HazardObservation
shape as HazardPolygonLayer below) without changing any caller.
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from app.config import HAZARD_CLASS_TO_SCORE, HAZARD_LEVEL_THRESHOLDS

DATA_DIR = Path(__file__).parent
HAZARD_LAYER_DIR = DATA_DIR / "hazard_layers"
DEFAULT_LANDSLIDE_LAYER_PATH = HAZARD_LAYER_DIR / "landslide_hazard_zonation.geojson"
DEFAULT_FLOOD_LAYER_PATH = HAZARD_LAYER_DIR / "flood_hazard_zonation.geojson"
DEFAULT_CLASS_COLUMN = "hazard_class"

LANDSLIDE_SOURCE_NAME = "APSAC landslide hazard zonation (Arunachal Pradesh SRSAC)"
FLOOD_SOURCE_NAME = "APSAC flood hazard zonation (Arunachal Pradesh SRSAC)"


class HazardLayerStatus(str, Enum):
    ok = "ok"  # a real classified value from an actually-loaded official layer
    no_coverage = "no_coverage"  # no layer loaded at all, OR a loaded layer simply doesn't cover this point


class HazardLevel(str, Enum):
    low = "low"
    moderate = "moderate"
    high = "high"
    very_high = "very_high"


@dataclass(frozen=True)
class HazardObservation:
    requested_lat: float
    requested_lon: float
    status: HazardLayerStatus
    source_class: Optional[str]  # the source layer's own class string, verbatim (e.g. "High") -- None if no_coverage
    hazard_level: Optional[HazardLevel]  # source_class mapped to a normalized display bucket -- None if no_coverage
    hazard_score: Optional[float]  # source_class mapped to a normalized [0,1] score -- None if no_coverage
    source: str


def _level_for_score(score: float) -> HazardLevel:
    if score >= HAZARD_LEVEL_THRESHOLDS["very_high"]:
        return HazardLevel.very_high
    if score >= HAZARD_LEVEL_THRESHOLDS["high"]:
        return HazardLevel.high
    if score >= HAZARD_LEVEL_THRESHOLDS["moderate"]:
        return HazardLevel.moderate
    return HazardLevel.low


def class_to_score(source_class: str) -> float:
    """Case-insensitive lookup into app/config.py::HAZARD_CLASS_TO_SCORE.
    Raises on an unrecognized class rather than guessing -- mirrors
    risk_engine.incident_factor_from_severity()'s raise-on-unknown
    convention elsewhere in this codebase."""
    key = source_class.strip().lower()
    if key not in HAZARD_CLASS_TO_SCORE:
        raise ValueError(
            f"Unrecognized hazard class {source_class!r}; expected one of "
            f"{sorted(HAZARD_CLASS_TO_SCORE)} (see app/config.py::HAZARD_CLASS_TO_SCORE -- "
            "update this mapping to match the real source layer's own vocabulary if it differs)"
        )
    return HAZARD_CLASS_TO_SCORE[key]


def _no_coverage(lat: float, lon: float, source: str) -> HazardObservation:
    return HazardObservation(
        requested_lat=lat, requested_lon=lon, status=HazardLayerStatus.no_coverage,
        source_class=None, hazard_level=None, hazard_score=None, source=source,
    )


class HazardPolygonLayer:
    """One polygon hazard-zonation layer (landslide OR flood -- this class
    doesn't know or care which). Wraps a GeoDataFrame; `gdf` staying `None`
    means "not loaded" -- every query against it is unconditionally
    no_coverage, never a fabricated low/0.0 score. Every class value found
    in a REAL loaded file is validated against HAZARD_CLASS_TO_SCORE at
    LOAD time (fail fast on an unrecognized vocabulary) rather than at
    query time."""

    def __init__(self, path: Optional[Path], class_column: str, source_name: str):
        self.path = Path(path) if path is not None else None
        self.class_column = class_column
        self.source_name = source_name
        self.gdf = None

        if self.path is not None and self.path.exists():
            import geopandas as gpd

            gdf = gpd.read_file(self.path)
            if class_column not in gdf.columns:
                raise ValueError(
                    f"{self.path}: expected a {class_column!r} hazard-class column, got columns {list(gdf.columns)}"
                )
            if gdf.crs is None:
                raise ValueError(f"{self.path}: layer has no CRS defined -- refusing to guess one")

            gdf = gdf.to_crs("EPSG:4326")
            classes = gdf[class_column].astype(str)
            unknown = sorted(set(classes) - {c for c in HAZARD_CLASS_TO_SCORE})
            # allow case differences without flagging them as "unknown"
            unknown = [c for c in unknown if c.strip().lower() not in HAZARD_CLASS_TO_SCORE]
            if unknown:
                raise ValueError(
                    f"{self.path}: unrecognized hazard class value(s) {unknown} in column {class_column!r} -- "
                    "update app/config.py::HAZARD_CLASS_TO_SCORE to match this file's real vocabulary"
                )
            gdf = gdf.copy()
            gdf["_hazard_score"] = classes.apply(lambda c: HAZARD_CLASS_TO_SCORE[c.strip().lower()])
            self.gdf = gdf

    @property
    def is_loaded(self) -> bool:
        return self.gdf is not None

    @property
    def feature_count(self) -> int:
        return 0 if self.gdf is None else len(self.gdf)

    def query(self, lat: float, lon: float) -> HazardObservation:
        if self.gdf is None:
            return _no_coverage(lat, lon, self.source_name)

        from shapely.geometry import Point

        point = Point(lon, lat)  # shapely Point(x, y) == (lon, lat) -- see landslide_mapper.py's same convention
        matches = self.gdf[self.gdf.contains(point)]
        if matches.empty:
            # A real layer IS loaded, but this point falls outside every
            # mapped polygon -- genuinely "not covered by this zonation
            # layer's mapped extent", not "confirmed low hazard".
            return _no_coverage(lat, lon, self.source_name)

        # Real GIS zonation layers occasionally have overlapping polygons at
        # boundaries; take the most conservative (highest-scoring) match
        # rather than an arbitrary "first row" -- consistent with this
        # project's general "most conservative reading wins" rule (see
        # core/hazard_state.py's multi-hazard MAX combination).
        best_idx = matches["_hazard_score"].idxmax()
        best_row = matches.loc[best_idx]
        score = float(best_row["_hazard_score"])

        return HazardObservation(
            requested_lat=lat, requested_lon=lon, status=HazardLayerStatus.ok,
            source_class=str(best_row[self.class_column]), hazard_level=_level_for_score(score),
            hazard_score=round(score, 4), source=self.source_name,
        )


class HazardLayerLoader:
    """The Part 11 entry point. Loads both hazard layers (each
    independently -- either, both, or neither may actually be present on
    disk) and answers point queries. See module docstring for the current
    real (both absent) status."""

    def __init__(
        self,
        landslide_layer_path: Optional[Path] = DEFAULT_LANDSLIDE_LAYER_PATH,
        flood_layer_path: Optional[Path] = DEFAULT_FLOOD_LAYER_PATH,
        landslide_class_column: str = DEFAULT_CLASS_COLUMN,
        flood_class_column: str = DEFAULT_CLASS_COLUMN,
    ):
        self.landslide_layer = HazardPolygonLayer(landslide_layer_path, landslide_class_column, LANDSLIDE_SOURCE_NAME)
        self.flood_layer = HazardPolygonLayer(flood_layer_path, flood_class_column, FLOOD_SOURCE_NAME)

    def get_landslide_hazard(self, lat: float, lon: float) -> HazardObservation:
        return self.landslide_layer.query(lat, lon)

    def get_flood_hazard(self, lat: float, lon: float) -> HazardObservation:
        return self.flood_layer.query(lat, lon)


_default_loader: Optional[HazardLayerLoader] = None


def get_default_hazard_layer_loader() -> HazardLayerLoader:
    """Process-wide singleton, mirroring dem_loader.get_default_dem_loader()
    / rainfall_loader.get_default_rainfall_loader(). Safe to call even when
    neither official layer is present -- both underlying HazardPolygonLayer
    instances simply stay unloaded, and every query returns no_coverage."""
    global _default_loader
    if _default_loader is None:
        _default_loader = HazardLayerLoader()
    return _default_loader
