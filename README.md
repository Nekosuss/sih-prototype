# AI-Based Smart Logistics and Accessibility Intelligence Platform for NER

SIH prototype demonstrating **hazard-aware routing**: a vehicle is routed
factoring in road-segment risk (terrain, weather, incidents), not just
distance, and reroutes automatically when conditions change.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design: architecture,
tech stack, folder structure, modules, data models, and communication flow.

## Status

Project structure scaffolded. Implementation not yet started.

## Layout

- `backend/` — FastAPI service (routing engine, risk engine, simulators, in-memory state store)
- `frontend/` — React + Leaflet SPA (map, vehicle panel, weather/incident controls, activity log)
