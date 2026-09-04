"""
POST /routes/calculate              compute the fastest (travel-time-only) route
POST /routes/calculate-risk-aware   compute + compare fastest vs. risk-aware routes (Part 6)
POST /routes/evaluate-disruption    CONTINUE / REROUTE / SUSPEND decision under current hazard state (Part 8)
GET  /routes/{route_id}             retrieve a previously calculated route (any of the above)

Thin by design: all pathfinding/geometry/risk logic lives in
core/routing_engine.py, core/risk_engine.py, core/reroute_service.py, and
core/hazard_state.py. This module only validates input, calls the engine,
and shapes the response.

/routes/calculate is UNCHANGED by Part 6/8 — same request/response shape,
same behavior (travel-time-only routing) — kept fully backwards-compatible.
/routes/calculate-risk-aware and /routes/evaluate-disruption are additive.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.routing_engine import (
    NoRouteFoundError,
    UnknownLocationError,
    calculate_route,
    compare_fastest_and_safe_routes,
    has_alternative_path,
)
from app.models.network import GeoPoint
from app.models.route import RiskAwareRouteResult, Route, RouteDecision
from app.store.state_store import state_store

router = APIRouter(prefix="/routes")


class RouteRequest(BaseModel):
    origin: str | GeoPoint
    destination: str | GeoPoint


class RouteCalculationResponse(BaseModel):
    route: Route
    # Whether the current road graph has any path-disjoint alternative
    # between these two points. Almost always False on today's single-chain
    # NER corridor (see backend/app/data/README.md) — exposed so the API
    # shape is already ready for real alternative routes once the network
    # gains real OSM branch roads, without fabricating any now. See
    # core/routing_engine.py::has_alternative_path.
    alternative_routes_available: bool


class RiskAwareRouteRequest(BaseModel):
    origin: str | GeoPoint
    destination: str | GeoPoint
    # Optional [0,1] current-context inputs (Part 5/6) — externally
    # supplied, NOT a trained rainfall/incident prediction. Omitted/None
    # means "no signal supplied," applied uniformly to every segment in the
    # network for this one request (no per-segment weather/incident
    # targeting exists yet — see core/routing_engine.py's risk-aware
    # routing section docstring).
    weather_factor: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    incident_factor: Optional[float] = Field(default=None, ge=0.0, le=1.0)


@router.post("/calculate", response_model=RouteCalculationResponse)
def calculate_route_endpoint(request: RouteRequest):
    try:
        route = calculate_route(
            state_store.graph,
            state_store.get_nodes(),
            state_store.get_segments(),
            request.origin,
            request.destination,
        )
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state_store.add_route(route)
    alternatives = has_alternative_path(state_store.graph, route.origin, route.destination)
    return RouteCalculationResponse(route=route, alternative_routes_available=alternatives)


@router.post("/calculate-risk-aware", response_model=RiskAwareRouteResult)
def calculate_risk_aware_route_endpoint(request: RiskAwareRouteRequest):
    """
    Computes the fastest route AND the risk-aware route, and reports which
    one is recommended (Part 6). See RiskAwareRouteResult.outcome for the
    three possible cases — `no_safe_route_available` is a normal 200
    response (a meaningful, structured outcome), not an error; only a bad
    location or a genuinely disconnected pair (no road at all, ignoring
    risk) is a 400.
    """
    try:
        result = compare_fastest_and_safe_routes(
            state_store.graph,
            state_store.get_nodes(),
            state_store.get_segments(),
            request.origin,
            request.destination,
            weather_factor=request.weather_factor,
            incident_factor=request.incident_factor,
        )
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state_store.add_route(result.fastest_route)
    if result.recommended_route is not None and result.recommended_route.route_id != result.fastest_route.route_id:
        state_store.add_route(result.recommended_route)
    return result


class EvaluateDisruptionRequest(BaseModel):
    origin: str | GeoPoint
    destination: str | GeoPoint
    # A route previously returned by /routes/calculate or
    # /routes/calculate-risk-aware (its route_id) — "the route currently in
    # effect," if any. Omit for a first-time evaluation (nothing to be
    # sticky about — see core/reroute_service.py).
    previous_route_id: Optional[str] = None
    # Same uniform current-context inputs as /routes/calculate-risk-aware,
    # applied on TOP of whatever the currently active simulated hazards
    # (GET /hazards) already contribute per-segment — see
    # core/hazard_state.py. Rarely needed alongside a hazard demo; kept for
    # parity/testing.
    weather_factor: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    incident_factor: Optional[float] = Field(default=None, ge=0.0, le=1.0)


@router.post("/evaluate-disruption", response_model=RouteDecision)
def evaluate_disruption_endpoint(request: EvaluateDisruptionRequest):
    """
    Part 8: CONTINUE / REROUTE / SUSPEND for this origin/destination given
    every currently ACTIVE simulated hazard (see POST /hazards/simulate).
    Like /routes/calculate-risk-aware, `suspend` is a normal 200 response,
    not an error — only a bad location or genuinely disconnected pair is a
    400.
    """
    previous_route = state_store.get_route(request.previous_route_id) if request.previous_route_id else None
    active_hazards = state_store.get_hazards(active_only=True)
    segment_context = combine_active_hazards_into_segment_context(active_hazards)

    try:
        decision = evaluate_route_decision(
            state_store.graph,
            state_store.get_nodes(),
            state_store.get_segments(),
            request.origin,
            request.destination,
            previous_route=previous_route,
            weather_factor=request.weather_factor,
            incident_factor=request.incident_factor,
            segment_context=segment_context,
            active_hazard_ids=[h.id for h in active_hazards],
        )
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if decision.recommended_route is not None:
        state_store.add_route(decision.recommended_route)
    return decision


@router.get("/{route_id}", response_model=Route)
def get_route(route_id: str):
    route = state_store.get_route(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Unknown route: {route_id}")
    return route
