"""
POST /vehicles              create a DETERMINISTIC SIMULATED vehicle (idle — see /start)
GET  /vehicles              list vehicles, each advanced to "now" first
GET  /vehicles/{id}         one vehicle, advanced to "now" first
POST /vehicles/{id}/start   begin movement (or resume after /pause)
POST /vehicles/{id}/pause   freeze movement in place
POST /vehicles/{id}/reset   recompute a fresh initial route and zero all progress/timing

Part 9. Every position/status here is a DETERMINISTIC SIMULATION — never
live GPS or real-time tracking (see app/models/vehicle.py). Thin by design,
matching every other routes_*.py module: all movement/interpolation logic
lives in app/simulation/vehicle_simulator.py, all routing/risk/hazard logic
in core/routing_engine.py, core/reroute_service.py, core/hazard_state.py —
this module only validates input, calls those, and shapes the response.

GET requests "tick" the vehicle (call advance_vehicle()) before returning
it — this is the whole simulation update mechanism: a client polling every
~1s always sees the vehicle brought up to the current instant, with no
background loop, WebSocket, or queue involved.
"""
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel

from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.core.routing_engine import NoRouteFoundError, UnknownLocationError
from app.models.network import GeoPoint
from app.models.vehicle import Vehicle
from app.simulation.vehicle_simulator import advance_vehicle, create_vehicle, pause_vehicle, reset_vehicle, start_vehicle
from app.store.state_store import state_store

router = APIRouter(prefix="/vehicles")


class CreateVehicleRequest(BaseModel):
    name: str
    origin: str | GeoPoint
    destination: str | GeoPoint
    # "risk-aware" (default): initial route is the safer of fastest/
    # risk-aware (Part 6), matching every other risk-aware surface in this
    # app. "fastest": travel-time-only initial route (Part 3) — route_risk
    # is still reported (informational), it just didn't influence path
    # selection. Either way, in-flight hazard response (reroute_service)
    # always applies once the vehicle is moving.
    mode: Literal["fastest", "risk-aware"] = "risk-aware"


def _current_hazard_context():
    active_hazards = state_store.get_hazards(active_only=True)
    segment_context = combine_active_hazards_into_segment_context(active_hazards)
    return segment_context, [h.id for h in active_hazards]


def _advance(vehicle: Vehicle) -> Vehicle:
    segment_context, active_hazard_ids = _current_hazard_context()
    return advance_vehicle(
        vehicle,
        state_store.get_nodes(),
        state_store.get_segments(),
        state_store.graph,
        segment_context=segment_context,
        active_hazard_ids=active_hazard_ids,
    )


def _get_or_404(vehicle_id: str) -> Vehicle:
    vehicle = state_store.get_vehicle(vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"Unknown vehicle: {vehicle_id}")
    return vehicle


@router.post("", response_model=Vehicle)
def create_vehicle_endpoint(request: CreateVehicleRequest):
    try:
        vehicle = create_vehicle(
            request.name,
            request.origin,
            request.destination,
            state_store.get_nodes(),
            state_store.get_segments(),
            state_store.graph,
            mode=request.mode,
        )
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state_store.add_vehicle(vehicle)
    return vehicle


@router.get("", response_model=list[Vehicle])
def list_vehicles():
    vehicles = [_advance(v) for v in state_store.get_vehicles()]
    for v in vehicles:
        state_store.add_vehicle(v)
    return vehicles


@router.get("/{vehicle_id}", response_model=Vehicle)
def get_vehicle_endpoint(vehicle_id: str):
    vehicle = _advance(_get_or_404(vehicle_id))
    state_store.add_vehicle(vehicle)
    return vehicle


@router.post("/{vehicle_id}/start", response_model=Vehicle)
def start_vehicle_endpoint(vehicle_id: str):
    vehicle = start_vehicle(_get_or_404(vehicle_id))
    vehicle = _advance(vehicle)
    state_store.add_vehicle(vehicle)
    return vehicle


@router.post("/{vehicle_id}/pause", response_model=Vehicle)
def pause_vehicle_endpoint(vehicle_id: str):
    vehicle = pause_vehicle(_get_or_404(vehicle_id))
    state_store.add_vehicle(vehicle)
    return vehicle


@router.post("/{vehicle_id}/reset", response_model=Vehicle)
def reset_vehicle_endpoint(vehicle_id: str):
    vehicle = reset_vehicle(_get_or_404(vehicle_id), state_store.get_nodes(), state_store.get_segments(), state_store.graph)
    state_store.add_vehicle(vehicle)
    return vehicle
