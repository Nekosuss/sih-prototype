# Production Roadmap & AI Engineering Blueprint

This document outlines the actionable, phased engineering roadmap required to transform the current single-corridor prototype into a production-grade, 8-state regional intelligence platform capable of winning the Smart India Hackathon.

---

### Phase 1: Database Persistence & Full NER Network Ingestion (Weeks 1–2)

#### 1.1 Migrate from In-Memory StateStore to PostgreSQL + PostGIS
- **Database:** PostgreSQL 16 with `PostGIS 3.4` and `pgRouting 3.6`.
- **Tables:**
  - `nodes`: `(id, name, geom GEOMETRY(Point, 4326), state, district)`
  - `road_segments`: `(id, source_node, target_node, cost, reverse_cost, geom GEOMETRY(LineString, 4326), road_type, slope_deg, elevation_m, landslide_count, status)`
  - `hazards`: `(id, type, severity, affected_segments, active, created_at, resolved_at)`
  - `field_reports`: `(id, reporter_id, geom GEOMETRY(Point, 4326), segment_id, photos TEXT[], status)`
  - `vehicles`: `(id, registration_no, cargo_type, current_geom, status, route_id)`
- **Pathfinding:** Leverage `pgr_dijkstra` directly inside PostGIS for lightning-fast edge evaluation over millions of road segments.

#### 1.2 Ingest Full 8-State North Eastern Region Road Network
- Download regional OSM `.pbf` extracts from Geofabrik for Assam, Arunachal Pradesh, Meghalaya, Manipur, Mizoram, Nagaland, Tripura, and Sikkim.
- Process via `osm2pgrouting` to build a topologically connected road network of all National Highways (NH-27, NH-13, NH-29, NH-102), State Highways, and Border Roads Organisation (BRO) mountain routes.

---

### Phase 2: True Machine Learning Disruption Model (Weeks 3–4)

#### 2.1 Assemble Supervised Training Dataset
Combine historical records across 2015–2024:
1. **Target ($Y$):** Historical landslide disruptions from GSI Bhukosh, BRO incident logs, and NDMA disaster reports.
2. **Dynamic Meteorological Features:** 1-day, 3-day, and 7-day trailing rainfall accumulations from IMD 0.25° gridded daily rainfall.
3. **Static Morphometric Features:** SRTM 30m slope gradient, curvature, Topographic Wetness Index (TWI), elevation, and GSI lithology / soil texture.
4. **Negative Sampling:** Extract comparable high-rainfall monsoon days on mountainous roads where no disruption occurred.

#### 2.2 Model Training & Calibration
- Train an **XGBoost / LightGBM Classifier** to output $P(\text{Disruption}_{24\text{h}} = 1)$.
- Implement calibration (Platt Scaling or Isotonic Regression) so the score represents a true mathematical probability.
- **Inference Integration:** Plug the model directly into `app/core/risk_engine.py` to replace the heuristic weights with $P(\text{Disruption})$:
  $$\text{Edge Cost} = \text{Travel Time} \times \Big(1.0 + 3.0 \cdot P(\text{Disruption})\Big)$$

---

### Phase 3: Real GPS Fleet Tracking & Live Weather APIs (Weeks 5–6)

#### 3.1 Hardware GPS & Mobile Telemetry Gateway
- **Protocols:** MQTT broker (Eclipse Mosquitto) or WebSockets accepting standard Traccar / NMEA / GeoJSON payloads.
- **Map-Matching:** Implement an open-source map-matching engine (e.g., Valhalla or OSRM Map Matching) using Hidden Markov Models (HMM) to snap noisy GPS coordinates to real road polylines.
- **Cargo-Specific Dispatch:** Add weight and dimension constraints for trucks carrying heavy construction materials vs. high-priority cold-chain pharmaceutical shipments.

#### 3.2 Live Weather & Forecast Ingestion
- Ingest real-time rainfall and 48-hour forward precipitation forecasts via:
  - IMD Nowcast Radar API / Meghdoot (ISRO / IMD)
  - ECMWF Open Data or GFS 0.25° Global Forecast System
- Dynamically update forward-looking segment weather factors based on expected arrival time at that segment.

---

### Phase 4: Field Mobile App, Offline Sync & Multilingual Alerts (Weeks 7–8)

#### 4.1 Progressive Web App (PWA) with Offline Synchronization
- Develop a mobile-optimized PWA using React + Tailwind or Flutter.
- **Offline Storage:** Use IndexedDB (via `Dexie.js` or `WatermelonDB`) to store cached road maps and pending incident reports.
- **Background Sync:** Implement Service Worker Background Sync API (`sync` event) to automatically upload offline field reports with photos once network connectivity (2G/4G) is re-established.
- **Photo Attachments:** Enable field workers to capture live road breach photos with automatic EXIF GPS extraction and client-side WebP compression.

#### 4.2 Multilingual Automated Alert Engine
- **Languages:** Assamese, Bengali, Bodo, Hindi, and English.
- **Delivery Channels:**
  - Emergency SMS alerts to drivers via CDAC Gov SMS Gateway or Twilio.
  - Push notifications to field engineers and district magistrates.
  - Automated WhatsApp alerts with alternate route map links.

#### 4.3 Centralized District Analytics Dashboard
- Choropleth GIS view of the 8 NER states displaying **District Accessibility Index (DAI)**:
  $$\text{DAI}_d = \frac{\text{Operational Inbound Road Capacity}}{\text{Baseline Road Capacity}} \times 100$$
- Visual alerts highlighting isolated administrative headquarters (e.g., Tawang, Anjaw, Kurung Kumey).
- Essential commodity inventory shortfall indicators based on delayed freight arrivals.
