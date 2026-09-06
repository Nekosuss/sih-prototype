# System Architecture & Geospatial Data Pipelines

This document details the architectural design, component communication flows, and data ingestion pipelines of the NER Logistics Platform.

---

### 1. Overall System Architecture

The platform is designed as a **modular monolith** optimized for auditable pathfinding, fast local iteration, and transparent algorithmic evaluation.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             REACT + VITE FRONTEND SPA                       │
│                                                                             │
│   MapView (Leaflet)  │  RoutePlanner  │  VehiclePanel  │  FieldReportPanel   │
│   AlertCenter        │  DataSources   │  EventTimeline │  SegmentDetailPanel │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ REST (HTTP/JSON Polling, 1-5s)
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                            FASTAPI BACKEND SERVICE                          │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │                        API ROUTERS (/app/api/)                      │   │
│   │   routes_network  │  routes_routing  │  routes_hazards              │   │
│   │   routes_vehicles │  routes_weather  │  routes_field_reports        │   │
│   │   routes_simulation (reset)                                         │   │
│   └──────────────────────────────────┬──────────────────────────────────┘   │
│                                      │                                      │
│   ┌──────────────────────────────────▼──────────────────────────────────┐   │
│   │                       CORE LOGIC (/app/core/)                       │   │
│   │   routing_engine.py      → Risk-penalized Dijkstra / A* over DiGraph│   │
│   │   risk_engine.py         → Explainable multi-factor scoring         │   │
│   │   reroute_service.py     → CONTINUE / REROUTE / SUSPEND evaluator   │   │
│   │   hazard_state.py        → Multi-event spatial context aggregator   │   │
│   │   field_report_service.py→ Geometric snapping (1km) & promotion     │   │
│   │   weather_factor.py      → IMD rainfall piecewise normalization     │   │
│   │   geo.py                 → Haversine & polyline vertex walker       │   │
│   └──────────────────────────────────┬──────────────────────────────────┘   │
│                                      │ Reads / Mutates                      │
│   ┌──────────────────────────────────▼──────────────────────────────────┐   │
│   │                  IN-MEMORY STATE STORE (/app/store/)                │   │
│   │   Road Network DiGraph  │  Segment Risk Cache  │  Active Hazards    │   │
│   │   Active Vehicles       │  Field Reports       │  Calculated Routes │   │
│   └──────────────────────────────────▲──────────────────────────────────┘   │
│                                      │ Ingests at startup                   │
│   ┌──────────────────────────────────┴──────────────────────────────────┐   │
│   │                     DATA LOADERS (/app/data/)                       │   │
│   │   osm_geojson_loader.py  ← guwahati_tawang_osm_corridor.geojson     │   │
│   │   dem_loader.py          ← SRTM .hgt.gz tiles (N26/N27 E91/E92)     │   │
│   │   landslide_mapper.py    ← GSI inventory & spatial join features    │   │
│   │   rainfall_loader.py     ← IMD 0.25° gridded daily rainfall CSV     │   │
│   │   hazard_layer_loader.py ← APSAC zonation interface (pluggable)     │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 2. Geospatial ETL Pipelines

The platform incorporates four distinct geospatial datasets into a unified topological graph at runtime.

#### Pipeline 1: OpenStreetMap Road Network Topology
* **Source:** OpenStreetMap extract (`guwahati_tawang_osm_corridor.geojson`, ~2.9 MB, ~2,000 ways, ~90,000 coordinate vertices).
* **Processing Module:** `app/data/osm_geojson_loader.py`.
* **Methodology:**
  1. **Intersection Detection:** Discovers coordinates shared across multiple ways (to 6 decimal places, ~0.11m precision).
  2. **Way Splitting:** Breaks raw OSM polylines into discrete directed edges (`RoadSegment`) between genuine junction nodes.
  3. **One-Way Handling:** Segments with `oneway == "yes"` produce a single directed edge. All other segments produce bidirectional edges.
  4. **Output:** 2,964 distinct road segments forming a fully connected `networkx.DiGraph`.

#### Pipeline 2: NASA SRTM Digital Elevation Model (DEM)
* **Source:** NASA SRTM 1-arc-second (~30m resolution) global elevation tiles, Skadi layout on AWS Open Data.
* **Coverage:** 4 tiles covering lat 26°N–28°N, lon 91°E–93°E:
  - `N26E091.hgt.gz`, `N26E092.hgt.gz`, `N27E091.hgt.gz`, `N27E092.hgt.gz`.
  - Stored locally under `backend/app/data/dem_cache/` (each 25.9 MB uncompressed, signed 16-bit big-endian).
* **Processing Module:** `app/data/dem_loader.py`, `dem_processor.py`.
* **Methodology:**
  1. **Polyline Resampling:** Road segments are subdivided at ~90m spacing.
  2. **Bilinear Interpolation:** Each point samples the 4 surrounding DEM grid cells. Void cells (-32768) are preserved and never fabricated.
  3. **Elevation:** Representative elevation is the arithmetic mean of valid samples along the segment.
  4. **Slope Magnitude (`slope_deg`):** Calculated as the mean absolute gradient:
     $$\text{slope}_{\%} = \frac{\sum |\Delta z|}{\sum \Delta d_{horizontal}} \times 100, \quad \theta_{\text{slope}} = \arctan\left(\frac{\text{slope}_{\%}}{100}\right)$$
     This preserves climbing and descending undulating mountain grades without canceling out.

#### Pipeline 3: GSI Historical Landslide Spatial Join
* **Source:** Geological Survey of India (GSI) National Landslide Susceptibility Mapping (NLSM) field inventory (`gsi_landslides_corridor.csv`).
* **Processing Module:** `app/data/landslide_mapper.py`, `landslide_corridor_validation.py`.
* **Methodology:**
  1. **Projected CRS Reprojection:** Reprojects WGS84 lat/lng coordinates to local UTM Zone 46N (EPSG:32646) for true meter-distance calculations.
  2. **Nearest Neighbor Join:** Performs `geopandas.sjoin_nearest` to identify the closest road segment within a 500m buffer.
  3. **Feature Derivation:** Aggregates `historical_landslide_count` and computes `nearest_landslide_distance_m`. Records > 500m are marked `UNMATCHED`.
  4. **Output:** Stored in `derived/road_landslide_features.csv` and merged onto segments at graph startup.

#### Pipeline 4: IMD Daily Gridded Rainfall (0.25° x 0.25°)
* **Source:** India Meteorological Department (Pune) Long Period Gridded Daily Rainfall NetCDF-3 (`ind2023_rfp25.nc`).
* **Extraction:** `backend/scripts/fetch_rainfall_data.py` extracts the corridor bounding box (70 grid cells x 365 days = 25,550 records) into `app/data/rainfall_corridor_2023.csv`.
* **Processing Module:** `app/data/rainfall_loader.py`, `app/core/weather_factor.py`.
* **Methodology:**
  1. **Midpoint Mapping:** Maps each segment's geometric midpoint to the nearest 0.25° IMD grid cell within 0.2° maximum distance.
  2. **Intensity Classification:** Converts daily precipitation ($mm/\text{day}$) into a normalized weather risk factor ($0.0 \le w \le 1.0$) using official IMD categorical thresholds.

---

### 3. Dynamic Hazard & State Lifecycle

```
[ Field Officer / Demo User ]
             │
             │ Submits Incident (lat, lng, severity)
             ▼
   [ routes_field_reports.py ]
             │
             │ Snaps to nearest segment (threshold ≤ 1.0 km)
             ▼
   [ field_report_service.py ]
             │
             │ Creates HazardEvent (type, severity, affected_segments)
             ▼
   [ StateStore._hazards ]
             │
             │ Triggers reroute evaluation
             ▼
   [ reroute_service.py ]
             │
             ├──► Infeasible or Blocking?  ──► SUSPEND (halt dispatch)
             ├──► ΔRisk > 0.05 vs Alt?     ──► REROUTE (switch to safe bypass)
             └──► Path remains safe        ──► CONTINUE (maintain route)
             │
             ▼
   [ React Frontend (AlertCenter / MapView) ]
             │
             └── Polls GET /hazards, GET /vehicles every 1-5s
```
