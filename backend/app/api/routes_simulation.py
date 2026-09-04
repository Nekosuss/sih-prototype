"""
POST /simulation/reset   restore the entire demo to a known baseline

Part 13 (originally sketched in ARCHITECTURE.md section 4/10, deferred until
now — see main.py's pre-Part-13 docstring). Reloads the real road network
and clears every piece of DYNAMIC state that has accumulated during a demo
session: simulated hazards (Part 8), field reports (Part 12), and simulated
vehicles (Part 9) -- so a presenter can return to a clean starting point
between runs, or recover mid-presentation if something drifts.

Does NOT touch any static source dataset (OSM GeoJSON, DEM cache, GSI CSV,
IMD rainfall cache, APSAC layer files) -- StateStore.load() only ever READS
those from disk; nothing here deletes or modifies them. Calculated routes
(StateStore._routes) are also cleared incidentally, since load() rebuilds
the store from scratch -- a stale route_id from before a reset simply
becomes unknown (404 on GET /routes/{id}), which is the correct, honest
behavior rather than silently keeping a route computed under pre-reset
hazard conditions.
"""
from fastapi import APIRouter

from app.store.state_store import state_store

router = APIRouter(prefix="/simulation")


@router.post("/reset")
def reset_simulation():
    state_store.load()
    return {
        "status": "ok",
        "segments_loaded": len(state_store.get_segments()),
        "active_hazards": 0,
        "active_field_reports": 0,
        "vehicles": 0,
    }
