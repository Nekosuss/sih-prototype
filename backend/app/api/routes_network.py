"""
GET /network                complete network: all nodes + all road segments
GET /segments                list of road segments only
GET /segments/{id}           one segment's full details
GET /segments/{id}/risk      one segment's Part 2 static base risk summary (unchanged)
GET /segments/{id}/risk-aware one segment's Part 5 explainable prototype risk,
                              automatically reflecting any currently ACTIVE
                              Part 8 simulated hazard on it

Reads only from StateStore; no computation beyond risk_engine.get_risk_summary
(a pure read-shaping function) and, for /risk-aware,
risk_engine.assess_segment_risk() with whatever weather_factor/incident_factor
the current hazard state resolves to for this one segment (see
core/hazard_state.py) — the SAME function every other risk-aware surface
uses, not a new calculation. Calling this before and after
POST /hazards/simulate is how the frontend's hazard panel shows a real
"before -> after" risk change for one segment without duplicating any risk
logic in JavaScript.
"""
from fastapi import APIRouter, HTTPException

from app.core import risk_engine
from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.models.risk import RiskResult
from app.store.state_store import state_store

router = APIRouter()


@router.get("/network")
def get_network():
    return {
        "nodes": state_store.get_nodes(),
        "segments": state_store.get_segments(),
    }


@router.get("/segments")
def list_segments():
    return state_store.get_segments()


@router.get("/segments/{segment_id}")
def get_segment(segment_id: str):
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")
    return segment


@router.get("/segments/{segment_id}/risk")
def get_segment_risk(segment_id: str):
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")
    return risk_engine.get_risk_summary(segment)


@router.get("/segments/{segment_id}/risk-aware", response_model=RiskResult)
def get_segment_risk_aware(segment_id: str):
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")

    active_hazards = state_store.get_hazards(active_only=True)
    context = combine_active_hazards_into_segment_context(active_hazards).get(segment_id)
    weather_factor = context.weather_factor if context else None
    incident_factor = context.incident_factor if context else None
    return risk_engine.assess_segment_risk(segment, weather_factor=weather_factor, incident_factor=incident_factor)
