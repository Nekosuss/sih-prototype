"""
GET /network              complete network: all nodes + all road segments
GET /segments              list of road segments only
GET /segments/{id}         one segment's full details
GET /segments/{id}/risk    one segment's current status/risk summary

Reads only from StateStore; no computation beyond risk_engine.get_risk_summary
(a pure read-shaping function, not a recompute).
"""
from fastapi import APIRouter, HTTPException

from app.core import risk_engine
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
