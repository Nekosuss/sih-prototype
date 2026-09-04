# Official hazard-zonation layers go here

This directory is where a REAL official landslide/flood hazard-zonation
file belongs, once obtained. See `backend/app/data/hazard_layer_loader.py`'s
module docstring for the full provenance/access-status writeup; summary:

- **Primary source**: Arunachal Pradesh State Remote Sensing Application
  Centre (APSAC/SRSAC) — https://www.srsac.arunachal.gov.in/admin/geospatial.html
  (mirror: `.../geospatial.php`). The catalogue lists a Landslide Hazard
  Zonation Map (1:50K state-wide; 1:10K for Tawang / West Kameng / East
  Kameng / Pakke-Kessang / Papumpare — this project's corridor districts)
  and a Flood Hazard Zonation Map (1:25K, state-wide).
- **Verified access status** (checked directly against both live pages):
  neither page offers a direct download link or file. Both require
  submitting a manual data request (an email / "Submit Data Request"
  contact-form workflow) rather than self-service download. The 1:10K
  district-level landslide zonation was itself listed as "Database will be
  ready by June, 2024" on the catalogue page.
- **No official file has been obtained or is bundled with this repository.**
  This directory is intentionally empty (aside from this README) for
  exactly that reason — nothing here should ever be presented as real
  Arunachal Pradesh hazard data.

## Expected file layout, once a real file is obtained

```
backend/app/data/hazard_layers/landslide_hazard_zonation.geojson
backend/app/data/hazard_layers/flood_hazard_zonation.geojson
```

(Any vector format `geopandas.read_file()` supports — GeoJSON, Shapefile,
GeoPackage, etc. — works; any CRS is fine, it is reprojected to EPSG:4326
on load.) Each file must carry a polygon attribute column identifying the
hazard class per feature (default expected column name: `hazard_class`,
configurable via `HazardLayerLoader`'s `landslide_class_column`/
`flood_class_column` constructor arguments). The class strings actually
present in that column must be covered by
`app/config.py::HAZARD_CLASS_TO_SCORE` (case-insensitive) — update that
table to match the real file's own vocabulary if it differs from the
default `very low / low / moderate / high / very high` scheme.

Once dropped in here, run:

```
cd backend
python -m app.data.hazard_layer_mapper
```

to regenerate `derived/road_hazard_layer_features.csv`, which
`network_loader.py` then merges onto every `RoadSegment`'s
`landslide_hazard_class`/`landslide_hazard_score`/`flood_hazard_class`/
`flood_hazard_score` fields at the next application start — no other code
needs to change.

## What this is NOT

`app/data/hazard_layer_loader.py` is unit-tested (`tests/test_hazard_layer.py`)
using tiny synthetic polygons constructed in-memory for those tests only.
Those synthetic geometries are never read by the running application and
must never be confused with real hazard data.
