# API & Data Specifications

This document defines the REST API endpoints, Pydantic domain models, and Frontend Component Architecture.

---

### 1. REST API Endpoint Catalog

Base URL: `http://localhost:8000` (or configured via `VITE_API_BASE`).

#### 1.1 Network & Road Segments
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `GET` | `/health` | Service health & total loaded segments count | None |
| `GET` | `/network` | Complete road network topology (Nodes + Segments) | None |
| `GET` | `/segments/{id}` | Detailed properties of a single road segment | Path: `id` (str) |
| `GET` | `/segments/{id}/risk` | Baseline static risk score (terrain only) | Path: `id` (str) |
| `GET` | `/segments/{id}/risk-aware` | Live contextual risk score (includes active hazards) | Path: `id` (str) |

#### 1.2 Routing & Disruption Evaluation
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `POST` | `/routes/calculate` | Baseline fastest route (travel time only) | `{ origin: str/GeoPoint, destination: str/GeoPoint }` |
| `POST` | `/routes/calculate-risk-aware` | Compare fastest vs. risk-aware routes | `{ origin, destination, weather_factor?, incident_factor? }` |
| `POST` | `/routes/evaluate-disruption` | Evaluate CONTINUE / REROUTE / SUSPEND | `{ origin, destination, previous_route_id? }` |
| `GET` | `/routes/{id}` | Retrieve previously calculated route object | Path: `id` (str) |

#### 1.3 Hazards & Hazard Zonation
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `POST` | `/hazards/simulate` | Trigger simulated hazard event on segments | `{ type, severity, affected_segment_ids: [str] }` |
| `GET` | `/hazards` | List active hazard events | Query: `active_only` (bool, default: true) |
| `POST` | `/hazards/{id}/clear` | Deactivate an active hazard event | Path: `id` (str) |
| `POST` | `/hazards/reset` | Clear all hazard history | None |
| `GET` | `/hazards/layers` | Status and provenance of APSAC zonation layers | None |
| `GET` | `/hazards/segments/{id}` | Static zonation & GSI history for a segment | Path: `id` (str) |

#### 1.4 Vehicle Movement & Simulation
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `POST` | `/vehicles` | Instantiate a new simulated vehicle (idle) | `{ name, origin, destination, mode: "fastest"\|"risk-aware" }` |
| `GET` | `/vehicles` | List all vehicles (advances position to "now") | None |
| `GET` | `/vehicles/{id}` | Get single vehicle state (advances to "now") | Path: `id` (str) |
| `POST` | `/vehicles/{id}/start` | Begin movement or resume paused vehicle | Path: `id` (str) |
| `POST` | `/vehicles/{id}/pause` | Freeze vehicle movement in place | Path: `id` (str) |
| `POST` | `/vehicles/{id}/reset` | Reset vehicle back to origin and recompute route | Path: `id` (str) |

#### 1.5 Weather & IMD Gridded Rainfall
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `GET` | `/weather/rainfall` | Query IMD rainfall at arbitrary coordinates | Query: `lat` (float), `lon` (float), `date` (YYYY-MM-DD) |
| `GET` | `/weather/segments/{id}` | Rainfall & weather factor at segment midpoint | Path: `id` (str), Query: `date` (YYYY-MM-DD) |
| `GET` | `/weather/corridor` | Corridor summary & high-rainfall segment list | Query: `date` (YYYY-MM-DD) |

#### 1.6 Field Incident Reporting
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `POST` | `/field-reports` | Submit field incident report (auto-snaps to road) | `{ incident_type, severity, latitude, longitude, description, reporter_name?, origin?, destination?, previous_route_id? }` |
| `GET` | `/field-reports` | List field reports | Query: `active_only` (bool, default: true) |
| `GET` | `/field-reports/{id}` | Get single field report details | Path: `id` (str) |
| `POST` | `/field-reports/{id}/resolve` | Mark incident resolved & clear its hazard | Path: `id` (str), Body: `{ origin?, destination?, previous_route_id? }` |

#### 1.7 Simulation Reset
| Method | Path | Description | Parameters / Body |
|---|---|---|---|
| `POST` | `/simulation/reset` | Restore backend demo state back to pristine baseline | None |

---

### 2. Primary Domain Data Models (Pydantic)

#### `RoadSegment` (`backend/app/models/network.py`)
```python
class RoadSegment(BaseModel):
    id: str
    from_node_id: str
    to_node_id: str
    distance_km: float
    estimated_travel_time_min: float
    geometry: list[GeoPoint]
    road_type: RoadType
    name: Optional[str]
    ref: Optional[str]
    bidirectional: bool
    elevation_m: Optional[float]
    slope_deg: Optional[float]
    historical_landslide_count: int = 0
    nearest_landslide_distance_m: Optional[float] = None
    landslide_hazard_class: Optional[str] = None
    landslide_hazard_score: Optional[float] = None
    flood_hazard_class: Optional[str] = None
    flood_hazard_score: Optional[float] = None
    status: Literal["open", "closed"] = "open"
```

#### `RiskResult` (`backend/app/models/risk.py`)
```python
class RiskResult(BaseModel):
    risk_score: float  # [0.0, 1.0]
    risk_level: RiskLevel  # low, moderate, high, critical
    reasons: list[str]
    breakdown: RiskBreakdown  # slope_risk, historical_landslide_risk, weather_risk, incident_risk
    metadata: dict[str, Any]
```

#### `RouteDecision` (`backend/app/models/route.py`)
```python
class RouteDecision(BaseModel):
    outcome: Literal["continue", "reroute", "suspend"]
    reason: str
    recommended_route: Optional[Route] = None
    current_route_risk: RouteRiskProfile
    recommended_route_risk: Optional[RouteRiskProfile] = None
    alternative_exists: bool
    disrupted_segment_ids: list[str]
```

---

### 3. Frontend Architecture

* **Framework:** React 18 with Vite.
* **Map Renderer:** `react-leaflet` wrapping Leaflet 1.9 + OpenStreetMap tiles.
* **State Management:** Local React state hoisted to `App.jsx`, synchronized via HTTP polling:
  - Vehicles polled at **1,000ms** intervals during movement.
  - Active hazards & field reports polled at **5,000ms** intervals.

#### Component Hierarchy
```
App.jsx (Root State & Polling Manager)
├── Header.jsx (Status indicator, active alert counter, Data Sources modal button, Reset Demo)
├── Left Rail:
│   ├── RoutePlanner.jsx (Origin/Destination dropdowns, Fastest vs Risk-Aware mode selector)
│   ├── WeatherControls.jsx (IMD observation date picker & location precipitation bars)
│   └── HazardControl.jsx (Simulation trigger for heavy rain, blockage, landslides)
├── Center:
│   └── MapView.jsx (Leaflet canvas, risk-colored polylines, vehicle marker, incident markers)
└── Right Rail:
    ├── SegmentDetailPanel.jsx (Detailed popup inspector for clicked segment)
    ├── AlertPanel.jsx (Active disruption warning banner)
    ├── AlertCenter.jsx (Consolidated severity-ordered list of active hazards & reports)
    ├── RouteSummary.jsx (Distance, ETA, and aggregate risk breakdown)
    ├── RouteComparison.jsx (Fastest vs Safe metrics side-by-side)
    ├── RiskBreakdown.jsx (Progress bars for terrain, GSI, weather, incident factors)
    ├── FieldReportPanel.jsx (Incident submission form with "Pick on Map" button)
    └── VehiclePanel.jsx (Simulated truck dispatch, start/pause controls, ETA tracker)
```
