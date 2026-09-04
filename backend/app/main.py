"""
FastAPI app entrypoint.

Loads the road network into the StateStore at startup and mounts
routes_network (network/segment data), routes_routing (route calculation,
including Part 8's disruption evaluation), routes_hazards (Part 8's
simulated hazard controls), routes_vehicles (Part 9's deterministic
simulated vehicle movement — polled, not a background loop; see
app/simulation/vehicle_simulator.py), routes_weather (Part 10's real IMD
rainfall endpoints — see app/data/rainfall_loader.py), and
routes_field_reports (Part 12's field-worker incident reporting, which feeds
the SAME hazard/risk/reroute pipeline as routes_hazards — see
app/core/field_report_service.py), and routes_simulation (Part 13's
POST /simulation/reset demo-baseline reset). Live GPS tracking is later
scope (see ARCHITECTURE.md) and is not mounted yet.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_field_reports,
    routes_hazards,
    routes_network,
    routes_routing,
    routes_simulation,
    routes_vehicles,
    routes_weather,
)
from app.store.state_store import state_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    state_store.load()
    yield


app = FastAPI(title="NER Hazard-Aware Logistics API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Vite picks the next free port (5174, 5175, ...) if 5173 is already in
    # use by another running instance, so match any localhost/127.0.0.1 dev
    # port rather than hardcoding 5173.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_network.router)
app.include_router(routes_routing.router)
app.include_router(routes_hazards.router)
app.include_router(routes_vehicles.router)
app.include_router(routes_weather.router)
app.include_router(routes_field_reports.router)
app.include_router(routes_simulation.router)


@app.get("/health")
def health():
    return {"status": "ok", "segments_loaded": len(state_store.get_segments())}
