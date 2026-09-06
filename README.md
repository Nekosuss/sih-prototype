# AI-Based Smart Logistics and Accessibility Intelligence Platform for North Eastern Region (NER)
## Problem Statement ID: 26002 | Ministry of Development of North Eastern Region (MDoNER)

> **Geotechnically Grounded, Multi-Stakeholder Logistics & Accessibility Intelligence Platform engineered for the mountain terrain, seasonal monsoons, and extreme logistical constraints of Northeast India.**

---

## Key Highlights

- 🏔️ **Geotechnically Grounded Terrain Modeling:** Samples real **NASA SRTM 1-arc-second (~30m) Digital Elevation Model (DEM)** tiles, computing true slope gradient magnitudes and physical elevations along road polylines.
- ⚠️ **GSI Historical Landslide Spatial Joins:** Integrates the official **Geological Survey of India (GSI)** historical landslide database, performing spatial buffer joins to compute landslide recurrence density and minimum proximity distance per road segment.
- 🌧️ **IMD Gridded Daily Rainfall Integration:** Ingests **India Meteorological Department (IMD) 0.25° x 0.25° gridded NetCDF-3** rainfall data to map precipitation thresholds into active soil saturation hazard factors.
- 🛣️ **Risk-Penalized Multiplicative Dijkstra Pathfinding:** Custom routing engine balancing travel time against multi-factor terrain risk ($\text{Cost} = t \times [1 + 2.0 \times \text{Risk}]$) to compute optimal, safe transport routes and bypass detours.
- 🔄 **Three-Tier In-Transit Disruption Protocol:** Evaluates newly emerging hazards against active convoys and executes decision-theoretic protocols (`CONTINUE`, `REROUTE`, or `SUSPEND`) with mathematical hysteresis ($0.05$) to eliminate route flapping.
- 📦 **Mission-Critical Commodity Priority Sensitivity:** Tailored routing for temperature-sensitive pediatric vaccines and medical supplies (extreme risk aversion), food grains (PDS), petroleum/POL, and heavy construction materials.
- 📱 **60-Second Mobile Field Incident Reporting:** Geometrically snaps field reports to the nearest road segment within 1 km, immediately injecting verified blockages into the live routing engine.
- 🏛️ **Decoupled 4-Workspace Architecture:** Cleanly separates Regional Command HQ (`/command`), Fleet & Route Dispatch (`/dispatch`), Mobile Field Reporting (`/field`), and SIH Simulation Lab (`/lab`).

---

## Canonical Problem Statement & Documentation Index

- 🎯 **[Official Problem Statement (PS 26002)](./docs/PROBLEM_STATEMENT.md)** — Canonical, immutable MDoNER problem statement specification.
- 📖 **[Master PS, Stakeholder & USP Analysis](./docs/COMPREHENSIVE_PS_STAKEHOLDER_USP_ANALYSIS.md)** — Deep problem breakdown, 6 stakeholder personas, 80-item granular app requirements catalog, 16-dimension competitive research, and 6 USPs.
- 🔍 **[Statutory Gap Analysis](./docs/SIH_PROBLEM_STATEMENT_GAP_ANALYSIS.md)** — Clause-by-clause evaluation against requirements (a) through (h) with engineering remedies.
- 🧭 **[User Flows & UI Architecture](./docs/USER_FLOWS_AND_UI_ARCHITECTURE.md)** — 6 detailed user journeys, sequence diagrams, and 4-workspace UI layout.
- ⚖️ **[Executive Summary & Codebase Truth Audit](./docs/EXECUTIVE_SUMMARY_AND_AUDIT.md)** — Unvarnished audit of real vs. simulated features.
- 📋 **[Feature Implementation Checklist](./docs/FEATURE_IMPLEMENTATION_CHECKLIST.md)** — Granular task status tracker.
- 🏗️ **[System Architecture & Data Pipelines](./docs/SYSTEM_ARCHITECTURE_AND_PIPELINES.md)** — Full ETL pipeline and system architecture specifications.
- 📐 **[Mathematical & Risk Models](./docs/MATHEMATICAL_AND_RISK_MODELS.md)** — Scientific formulations for slope, risk scoring, Dijkstra costs, and hysteresis.
- 🔌 **[REST API & Data Specifications](./docs/API_AND_DATA_SPECIFICATIONS.md)** — Complete API endpoint dictionary and schema definitions.
- 🚀 **[Production Roadmap & AI Engineering Blueprint](./docs/PRODUCTION_ROADMAP_AND_AI_BLUEPRINT.md)** — Technical roadmap to scale to all 8 NER states.

---

## Quick Start Guide

### 1. Python Backend Service (FastAPI)
```bash
cd backend
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
python -m pytest tests/              # Run automated backend test suites
python -m uvicorn app.main:app --reload --port 8000
```
Interactive Swagger API documentation is available at `http://127.0.0.1:8000/docs`.

### 2. React + Leaflet Frontend Application (Vite)
```bash
cd frontend
npm install
npm run dev                          # Starts frontend at http://localhost:5173
```
