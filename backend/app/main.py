"""
FastAPI app entrypoint.

Loads the road network into the StateStore at startup and mounts
routes_network (network/segment data) and routes_routing (route
calculation). Vehicle dispatch, weather/incident routes, the simulator
loops, and /simulation/reset are later scope (see ARCHITECTURE.md) and are
not mounted yet.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_network, routes_routing
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


@app.get("/health")
def health():
    return {"status": "ok", "segments_loaded": len(state_store.get_segments())}
