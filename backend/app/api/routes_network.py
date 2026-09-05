"""
GET /network                complete network: all nodes + all road segments
GET /segments                list of road segments only
GET /segments/{id}           one segment's full details
GET /segments/{id}/risk      one segment's Part 2 static base risk summary (unchanged)
GET /segments/{id}/risk-aware one segment's Part 5 explainable prototype risk,
                              automatically reflecting any currently ACTIVE
                              Part 8 simulated hazard on it
GET /segments/{id}/ml-risk   one segment's Part 15B advisory ML risk signal
                              (Part 15C) -- see the dedicated docstring below;
                              completely separate from every endpoint above

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
from app.core.ml_risk_signal import get_ml_risk_signal
from app.models.ml_risk import MLRiskSignal
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


@router.get("/segments/{segment_id}/ml-risk", response_model=MLRiskSignal)
def get_segment_ml_risk(segment_id: str):
    """
    Part 15C: exposes the isolated, advisory ML risk signal (Part 15B —
    core/ml_risk_signal.py) for one real segment. This is the ONLY thing
    this endpoint does: look up the real segment, call
    ml_risk_signal.get_ml_risk_signal(), return its MLRiskSignal as-is.

    It does NOT call risk_engine.py, does NOT call routing_engine.py, and
    computes no new risk score of its own — see GET /segments/{id}/risk
    and GET /segments/{id}/risk-aware above for the authoritative risk
    score, which this endpoint neither reads nor affects.

    `MLRiskSignal.available=False` (e.g. ML_RISK_ENABLED is False, the
    artifact is unavailable, or this segment's features can't be honestly
    computed) is a normal 200 response — a structured, expected outcome
    for this endpoint to report, exactly like
    RiskAwareRouteResult.outcome == "no_safe_route_available" is a normal
    200 in routes_routing.py, not an error. A 404 is reserved for an
    unknown segment_id, matching every other /segments/{id}/* endpoint
    above.

    `score` (when present) is the model's raw ranking output — see
    MLRiskSignal's own docstring (app/models/ml_risk.py): NOT a calibrated
    probability, NOT a probability of landslide, NOT a percentage chance.
    This endpoint never relabels or rescales it.
    """
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")
    return get_ml_risk_signal(segment)
