"""
POST /routes/calculate   compute a route between an origin and destination
GET  /routes/{route_id}  retrieve a previously calculated route

Thin by design: all pathfinding/geometry logic lives in core/routing_engine.
This module only validates input, calls the engine, and shapes the response.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.routing_engine import (
    NoRouteFoundError,
    UnknownLocationError,
    calculate_route,
    has_alternative_path,
)
from app.models.network import GeoPoint
from app.models.route import Route
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


@router.get("/{route_id}", response_model=Route)
def get_route(route_id: str):
    route = state_store.get_route(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Unknown route: {route_id}")
    return route
