# ARCHITECTURE.md

## AI-Based Smart Logistics and Accessibility Intelligence Platform for NER
### Prototype Architecture (SIH)

This document describes the architecture for a **proof-of-concept prototype**, not a
production platform. It exists to demonstrate one thing convincingly:
**hazard-aware routing beats shortest-distance routing** when road conditions change.

Every decision below is filtered through that goal. If a component doesn't help
demonstrate the routing/risk/reroute story, it isn't in scope.

---

## 1. Architectural Style

**Modular monolith, not microservices.**

- One backend process (FastAPI) containing clearly separated internal modules
  (`api`, `core`, `simulation`, `models`, `store`).
- One frontend SPA (React) that talks to that backend over plain REST + polling.
- No message queues, no service mesh, no auth service, no separate databases per
  domain. A student prototype judged on a demo does not benefit from distributed
  systems complexity — it benefits from a codebase a judge (or teammate) can read
  top to bottom in ten minutes.

Modules communicate via **direct Python function calls** within the backend process
(not network calls), coordinated by a single **in-memory state store**. This keeps
the "engine" code (routing, risk) pure, testable, and honest — it's real logic, not
a thin wrapper around a database query.

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React SPA)                     │
│  MapView │ VehiclePanel │ WeatherControls │ IncidentForm │ Log    │
└───────────────────────────────┬───────────────────────────────────┘
                                 │ REST (JSON) + short-interval polling
┌───────────────────────────────▼───────────────────────────────────┐
│                        BACKEND (FastAPI, single process)          │
│                                                                     │
│   api/            → HTTP routes, request/response validation      │
│   core/            ─────────────────────────────────────────┐     │
│     routing_engine   (Dijkstra/A* over risk-weighted graph)  │     │
│     risk_engine      (explainable risk scoring, pluggable    │     │
│                        model: rule-based now, ML-ready later)│     │
│     reroute_service   (decides: is current route still safe?)│     │
│   simulation/                                                 │     │
│     vehicle_simulator (moves vehicles along their route)     │     │
│     weather_simulator (injects/evolves weather events)       │     │
│   store/            → single in-memory StateStore (source of  │     │
│                        truth: network, vehicles, incidents,   │     │
│                        weather, risk scores, event log)       │     │
│   models/           → Pydantic schemas shared across modules  │     │
│   data/              → static seed data (NER road network)    │     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Backend language | Python 3.11+ | Readable, fast to iterate, great graph/algorithm ergonomics |
| Backend framework | FastAPI | Async, automatic OpenAPI docs (useful for a judge demo), Pydantic validation built in |
| Graph representation | `networkx` | Battle-tested graph structure + shortest-path primitives; we still supply **our own explainable weight function**, so the routing cost logic stays transparent, not a black box |
| Server | Uvicorn | Standard ASGI server for FastAPI |
| State/storage | In-memory Python objects (`StateStore` singleton) | No DB needed for a demo; state resets on restart, which is fine for a prototype. Avoids an entire persistence layer that adds no value to the demo story |
| Frontend framework | React + Vite | Fast dev loop, minimal config, everyone on an SIH team can read it |
| Map rendering | `react-leaflet` + Leaflet + OpenStreetMap tiles | Free, no API key required, good enough for NER coordinates |
| HTTP client | `axios` (or plain `fetch`) | Simple, no need for a heavier data-fetching library |
| Live updates | Polling (`setInterval`, ~2s) against REST endpoints | A WebSocket layer would look more "real-time" but adds real complexity for a demo that already has a fast enough network. Polling is simple and transparent — explicitly avoiding overengineering here |
| Styling | Plain CSS (or Tailwind if the team prefers) | No design system needed for a POC |

**Explicitly not used:** databases (Postgres/Mongo), auth/JWT, Docker/K8s, message
queues, microservices, blockchain, payment systems, complex fleet-management
features. None of these serve the core demo.

Optional stretch (not needed for the prototype, noted for completeness): persist
incidents to a single SQLite file so a restart doesn't lose demo data. Skip unless
there's time left over.

---

## 3. Folder Structure

```
North/
├── ARCHITECTURE.md
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                 # FastAPI app entrypoint, mounts routers, starts sim loop
│   │   ├── config.py                # constants: risk thresholds, weights, tick interval
│   │   ├── api/
│   │   │   ├── routes_network.py    # GET road network (nodes/segments + live risk)
│   │   │   ├── routes_vehicles.py   # dispatch vehicle, get vehicle state, route history
│   │   │   ├── routes_weather.py    # trigger/simulate weather events
│   │   │   ├── routes_incidents.py  # report + list geo-tagged incidents
│   │   │   ├── routes_activity.py   # combined chronological event log
│   │   │   └── routes_simulation.py # POST /simulation/reset — restore known initial state
│   │   ├── core/
│   │   │   ├── risk_engine.py       # explainable per-segment risk score; pluggable model
│   │   │   │                        # (RuleBasedRiskModel now, MLRiskModel later)
│   │   │   ├── routing_engine.py    # risk-weighted shortest path (Dijkstra/A*)
│   │   │   └── reroute_service.py   # decides when + how to reroute a vehicle
│   │   ├── simulation/
│   │   │   ├── vehicle_simulator.py # advances vehicle position along its route
│   │   │   └── weather_simulator.py # generates/evolves weather + hazard events
│   │   ├── models/
│   │   │   ├── network.py           # Node, RoadSegment
│   │   │   ├── risk.py              # RiskScore, RiskFactor breakdown
│   │   │   ├── vehicle.py           # Vehicle, Route, RerouteEvent
│   │   │   ├── weather.py           # WeatherCondition
│   │   │   └── incident.py          # Incident
│   │   ├── store/
│   │   │   └── state_store.py       # single in-memory source of truth; supports reset()
│   │   └── data/
│   │       ├── network_loader.py    # loads a network into the StateStore at startup —
│   │       │                        # the swap point for a future OSM-derived dataset
│   │       └── ner_road_network.json # seed graph: hand-curated NER towns/roads
│   └── tests/
│       └── (unit tests for risk_engine + routing_engine — the "real" logic)
└── frontend/
    ├── package.json
    ├── index.html
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── App.jsx                  # layout: map + side panels, polling loop
        ├── api/
        │   └── client.js             # thin fetch/axios wrapper for backend REST API
        ├── components/
        │   ├── MapView/              # renders network colored by risk + vehicle marker
        │   ├── VehiclePanel/         # current route, distance, risk, reroute banner
        │   ├── WeatherControls/      # demo controls: trigger weather/hazard on a segment
        │   ├── IncidentForm/         # field-officer incident report (click map → form)
        │   └── ActivityLog/          # chronological feed: weather → risk → reroute → incident
        └── styles/
            └── index.css
```

---

## 4. Backend Modules

### `models/`
Pydantic schemas shared everywhere. These are the vocabulary the rest of the system
speaks — see Section 6.

### `store/state_store.py`
A single in-process object holding current truth: the road network graph, live risk
scores per segment, active vehicles + their routes, weather conditions, incidents,
and a chronological activity log. Every other module reads/writes through this
store instead of passing state around ad hoc. This is intentionally the *only*
piece of "infrastructure" in the whole backend — the extensibility points
described below (pluggable risk model, swappable network loader) are about
interfaces, not about adding real infrastructure now. The store stays in-memory,
and weather/vehicle data stays simulated, by design (see Section 8).

The store also exposes a `reset()` operation: reload the network from
`network_loader`, clear vehicles/weather/incidents/activity log, and re-seed
whatever initial demo state the app starts with. This backs the
"Reset Simulation" capability (Section 4, `api/routes_simulation.py`) so a
presentation can always return to a known starting point.

### `data/network_loader.py`
Loads the road network into the `StateStore` at startup. For the prototype this
reads the hand-curated `ner_road_network.json` seed file. It is deliberately the
**only** place that knows where network data comes from — `routing_engine` and
`risk_engine` only ever see `Node`/`RoadSegment` objects out of the `StateStore`,
never a file format. That seam is what lets the seed file later be replaced by a
loader that ingests a real OpenStreetMap-derived extract for the NER region
(converted to the same `Node`/`RoadSegment` schema) without touching any routing
or risk logic.

### `core/risk_engine.py`
Computes a **transparent** risk score per road segment. This is the prototype's
**initial explainable risk model** — a rule-based baseline, not a claim of "AI."
It is intentionally simple and auditable so every score can be justified to a
judge. Whether it's later replaced or augmented by a machine-learning disruption
predictor is a separate, later evaluation (see below) — it is not part of this
prototype's scope.

```
total_risk = clamp(base_risk + weather_factor + incident_factor, 0, 1)
```

- `base_risk`: a static per-segment terrain/hazard profile, derived from the
  segment's `terrain_type`, `slope`, `elevation`, `landslide_susceptibility`, and
  `flood_susceptibility` fields (see Section 6). Keeping these as separate fields
  — rather than one coarse `terrain_type` enum — matters for the formula: e.g.
  `weather_factor` for a rain event can be scaled up on a segment with high
  `landslide_susceptibility` and left low on a flat segment with the same rain,
  which a single category label couldn't express.
- `weather_factor`: from the current `WeatherCondition` on that segment (e.g. clear
  = 0.0, rain = 0.2, heavy rain = 0.4, fog = 0.15, snow = 0.3), optionally scaled
  by the relevant susceptibility field as above.
- `incident_factor`: from any active `Incident` on that segment, scaled by
  severity (minor = 0.2, major = 0.5, blocking = 1.0) and linearly decayed back to
  0 over a configured simulated time window.

Every `RiskScore` retains the individual factor breakdown, not just the total —
that breakdown is what lets the UI (and the judges) see *why* a segment is risky,
which is the whole point of "explainable."

**Pluggable risk model, designed but not built yet.** `risk_engine` exposes a
single stable function, `get_risk(segment_id) -> RiskScore`, and that is the
*only* thing any other module (`routing_engine`, `reroute_service`, the API
routers) is allowed to call — nothing outside `risk_engine` reads raw weather or
incident data directly. Internally, today's implementation is a
`RuleBasedRiskModel` applying the formula above. Because every consumer only ever
sees `RiskScore` objects out of `get_risk()`, a future `MLRiskModel` (or a hybrid
that blends a learned disruption-probability signal with the rule-based factors)
can implement the same call signature and be swapped in later via a config flag
— with **zero changes to `routing_engine` or `reroute_service`**, since they only
consume `total_risk`/`RiskScore`, never the factors that produced it.

(`risk_engine` also exposes `recompute(segment_id)`, the write-side counterpart
called by the weather/incident handlers whenever an input changes — see Section 7.
Only `recompute` touches the model; every other caller only ever reads via
`get_risk`.)

### `core/routing_engine.py`
Wraps the road network in a `networkx` graph and computes shortest paths using an
edge-cost function — **not raw distance**:

```
edge_cost = distance_km * (1 + RISK_WEIGHT * segment_risk)
```

where `segment_risk` comes from `risk_engine.get_risk(segment_id).total_risk` and
`RISK_WEIGHT` is a single tunable constant in `config.py`. This is the prototype's
core differentiator, stated plainly: routes are chosen to minimize a cost that
blends distance and risk, so a shorter-but-dangerous segment can lose to a
longer-but-safer one. The function that computes cost is ordinary, readable
Python — nothing hidden, and nothing here depends on *how* `segment_risk` was
computed, which is exactly what keeps it decoupled from `risk_engine`'s internals.

### `core/reroute_service.py`
Given a vehicle's active route, checks whether it's still the best option:
1. Recompute the risk-weighted cost of the *remaining* portion of the current route.
2. Recompute the best route from the vehicle's current position to its destination.
3. If the alternative is meaningfully cheaper (beyond a small hysteresis margin, to
   avoid flapping), or if any upcoming segment's risk crosses a hard "unsafe"
   threshold, trigger a reroute: update the vehicle's active route, and append a
   `RerouteEvent` (with a human-readable reason) to the activity log.

This module is what's called after every weather change and every incident report
— it's the bridge between "risk changed" and "vehicle got rerouted."

### `simulation/vehicle_simulator.py`
A background loop (an `asyncio` task started at app startup) that, on each tick,
advances each active vehicle a small distance along its current route's geometry
and updates its lat/lng in the store. After moving, it asks `reroute_service` to
re-check safety — this is what makes rerouting feel automatic in the demo rather
than something a user has to click for.

### `simulation/weather_simulator.py`
Exposes a function to set/change a `WeatherCondition` on a segment or region,
either via an explicit API call (demo control button) or an optional background
tick that randomly evolves conditions. For the prototype, **manual, judge-visible
triggering is preferred** over pure randomness — a demo should be able to reliably
reproduce "watch it reroute" on cue.

### `api/routes_simulation.py`
`POST /simulation/reset` — calls `StateStore.reset()` to return the entire demo
(network risk state, vehicles, weather, incidents, activity log) to a known
initial state. Lets a presenter re-run the scenario cleanly between demo runs,
or recover mid-presentation if something drifts.

### `api/`
Thin FastAPI routers. Each route validates input with Pydantic, calls into
`core`/`simulation`/`store`, and returns a schema — no business logic lives here.

---

## 5. Frontend Modules

- **MapView** — Leaflet map rendering the NER road network as colored polylines
  (green/amber/red by current risk), plus the vehicle marker at its live position
  and the active route highlighted. Clicking the map in "report incident" mode
  captures a lat/lng for the incident form.
- **VehiclePanel** — shows the selected vehicle's current route, total distance,
  total risk, ETA, and a banner when a reroute just happened ("Rerouted: NH-X
  risk rose to HIGH due to heavy rainfall near Segment 4").
- **WeatherControls** — demo control panel: pick a segment/region and a weather
  type/intensity, submit to the backend. This is the "make something happen"
  button for the presentation.
- **IncidentForm** — field-officer simulation: pick a location (via map click or
  segment dropdown), type (landslide/flood/accident/roadblock), severity,
  description; submits to `POST /incidents`.
- **ActivityLog** — a scrolling chronological feed built from the backend's
  activity log: weather changes → risk updates → reroute decisions → incident
  reports, each with a plain-English reason. This view is what makes the causal
  chain (step 3 → step 9 in the scenario) visible and explainable to a judge.

- **Reset Simulation** — a single button (in `App.jsx`'s header, alongside the
  other demo controls) that calls `POST /simulation/reset` and refreshes all
  panels. Lets a presenter return to a known starting state before or during a
  demo without restarting the backend process.

`App.jsx` owns a single polling loop (`setInterval`, ~2s) that re-fetches network
state, vehicle state, and the activity log, and passes them down as props — no
state-management library needed for this scope.

---

## 6. Data Models

```
Node
  id, name, lat, lng, type            # depot | town | junction

RoadSegment
  id, from_node_id, to_node_id
  distance_km
  geometry: [ {lat, lng}, ... ]        # polyline points for map rendering
  terrain_type                          # plain | hill | mountain
  slope                                 # degrees (or category) — extensible toward real DEM data
  elevation                             # meters
  landslide_susceptibility             # 0.0–1.0 — extensible toward real hazard zonation data
  flood_susceptibility                 # 0.0–1.0 — extensible toward real flood inundation data
  base_risk                            # static component, derived from the fields above

WeatherCondition
  segment_id
  condition_type                       # clear | rain | heavy_rain | fog | snow
  intensity                            # 0.0–1.0
  updated_at

Incident
  id, segment_id, lat, lng
  type                                  # landslide | flood | accident | roadblock
  severity                             # minor | major | blocking
  description, reported_by, reported_at

RiskScore
  segment_id
  base_risk, weather_factor, incident_factor
  total_risk                           # clamp(sum, 0, 1)
  level                                 # low | medium | high (derived, for display)
  updated_at

Vehicle
  id, name, cargo_type
  current_lat, current_lng, current_segment_id
  status                                # idle | en_route | rerouted | arrived
  origin_node_id, destination_node_id
  active_route_id

Route
  id, vehicle_id
  segment_ids: [...]
  total_distance_km, total_risk_cost
  created_at, is_active
  superseded_by_route_id (nullable)

RerouteEvent
  id, vehicle_id
  old_route_id, new_route_id
  trigger_segment_id, reason           # human-readable explanation
  timestamp
```

---

## 7. How the Pieces Communicate

The whole scenario is one causal chain running through the `StateStore`:

1. **Dispatch** — `POST /vehicles/{id}/dispatch` calls `routing_engine` to compute
   the initial risk-weighted route from origin to destination; stored as the
   vehicle's active `Route`.
2. **GIS layer** (the road network + geometry) is static seed data
   (`ner_road_network.json`) loaded into the `StateStore` at startup by
   `network_loader.py`; the frontend `MapView` fetches it once via `GET /network`
   and re-fetches risk levels on each poll.
3. **Vehicle simulator** ticks in the background, moving the vehicle along its
   route's geometry and updating `current_lat/lng` in the store.
4. **Weather change** — a demo action (`POST /weather/events`) or the weather
   simulator updates a segment's `WeatherCondition`.
5. This immediately triggers `risk_engine.recompute(segment_id)`, updating that
   segment's `RiskScore` in the store.
6. Any risk recompute triggers `reroute_service.check(vehicle_id)` for every
   vehicle whose active route touches that segment.
7. If `reroute_service` decides the current route is no longer best, it calls
   `routing_engine` again from the vehicle's current position, installs the new
   `Route`, marks the old one superseded, and appends a `RerouteEvent`.
8. **Incident report** — `POST /incidents` from the field-officer form maps the
   reported lat/lng to the nearest `RoadSegment` (simple nearest-point-on-polyline
   check against the static geometry), stores the `Incident`, and runs the exact
   same steps 5–7. This is why incidents and weather share one pipeline instead of
   two separate ones — they're just two different sources of "a segment's risk
   changed."
9. The frontend never computes risk or routes itself — it only polls
   `GET /network`, `GET /vehicles`, and `GET /activity-log` and renders what the
   backend decided. This keeps all routing/risk decision logic entirely
   server-side and testable.
10. **Reset** — `POST /simulation/reset` calls `StateStore.reset()`, which
    reloads the network via `network_loader`, clears vehicles/weather/incidents/
    activity log, and re-seeds the initial demo state. The next poll picks up the
    reset state exactly like any other change.

```
WeatherControls / IncidentForm (frontend)
        │  POST
        ▼
   api/routes_*.py
        │
        ▼
 risk_engine.recompute(segment)  ──► StateStore.risk_scores updated
        │
        ▼
 reroute_service.check(vehicle)  ──► routing_engine.shortest_path(...)
        │                                    │
        │  (if better route found)           │ (uses current risk_scores)
        ▼                                    ▼
 StateStore.vehicles[...].active_route   networkx graph + edge_cost()
        │
        ▼
 StateStore.activity_log.append(RerouteEvent)
        │
        ▼
 MapView / VehiclePanel / ActivityLog (frontend, via polling GET endpoints)
```

---

## 8. Real vs. Simulated Data

| Component | Real logic? | Data source |
|---|---|---|
| Routing algorithm (Dijkstra/A* over risk-weighted graph) | **Real** | Computed live from current graph + risk scores |
| Risk scoring formula | **Real** (initial rule-based model) | Computed live from terrain/hazard fields + weather + incidents, fully explainable breakdown; exposed behind a stable interface so it can later be replaced/augmented by an ML disruption-prediction model without touching `routing_engine` |
| Reroute decision logic | **Real** | Computed live by comparing current vs. best alternative route cost |
| Incident → nearest-segment mapping | **Real** | Geometric nearest-point calculation against actual segment polylines |
| Road network topology (NER towns/roads) | Simulated data, real structure | Hand-curated JSON seed file with plausible NER locations/connections, loaded through `network_loader.py`. Not pulled from a live GIS/mapping API for the prototype, but structured exactly like real road-segment data would be, so a later swap to an OpenStreetMap-derived NER extract only requires changing the loader, not `routing_engine`/`risk_engine` |
| Weather events | Simulated | Manually triggered via demo controls (optionally auto-generated); modeled with the same schema real weather-API data would use, so swapping in a live feed later is a data-source change, not a logic change |
| Vehicle GPS movement | Simulated | Server-side tick loop moves the vehicle along route geometry; same `current_lat/lng` shape a real GPS device would report |
| Incidents | Simulated input, real handling | Submitted through the UI as a stand-in for a field officer's mobile report; once submitted, handled by the same real risk/reroute pipeline as any other data source |

The dividing line is deliberate: **anything that decides something (routing, risk,
rerouting) is real code with real logic; anything that generates raw facts about
the world (GPS position, weather, road topology) is simulated but shaped exactly
like the real data would be**, so the decision-making parts of the system are
never the part that's faked.

Note on the risk model specifically: today's `risk_engine` is a hand-written,
rule-based scoring formula — explainable by design, and not described as "AI"
anywhere in this system. It is built behind a stable interface
(`get_risk(segment_id) -> RiskScore`, see Section 4) specifically so that a later
phase can evaluate replacing or augmenting it with a trained disruption-prediction
model without changing `routing_engine`, `reroute_service`, or any API contract.
That evaluation is out of scope for this prototype.
