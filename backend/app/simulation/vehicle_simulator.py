"""
Part 9: DETERMINISTIC SIMULATED vehicle movement along a real route.

This is NOT live GPS or real-time vehicle tracking. A Vehicle's position is
always recomputed FRESH from (a) the real geometry of its currently
assigned route (core/routing_engine.py — never fabricated) and (b) real
elapsed wall-clock time since that route was assigned, converted to
distance via a configurable simulated speed
(app/config.py::SIMULATION_SPEED_KMPH). There is no background loop, no
WebSocket, no queue: the frontend polls (e.g. every ~1s), and each poll's
handler (api/routes_vehicles.py) calls advance_vehicle() to bring the
vehicle's stored state up to "now" before returning it. Recomputing fully
from a fixed time anchor on every call — rather than incrementally adding
a small delta each tick — is deliberate: it avoids the cumulative
floating-point drift a repeated-addition approach would risk over a long
demo session.

--- Route following (section 3) ---

Position is interpolated along the REAL combined route geometry
(Route.geometry, itself built from real per-segment OSM geometry by
core/routing_engine.py) via core/geo.py::interpolate_along_path — never a
straight line between towns, never random coordinates.

--- Hazard awareness (section 8) ---

On every advance, IF there is at least one active simulated hazard (Part
8) AND at least one of it touches a segment still AHEAD of the vehicle (or
the vehicle is currently `suspended`, in which case we always re-check —
a cleared hazard must be detectable), this module reuses
core/reroute_service.py::evaluate_route_decision() UNCHANGED — no
competing decision logic — against ONLY the unfinished remainder of the
vehicle's route (core/routing_engine.py::build_remaining_route()), so a
hazard on an already-passed segment can never retroactively suspend or
reroute a vehicle that already drove safely past it.

--- Reroute handling: a deliberate simplification ---

When a reroute is warranted, this module does NOT splice the new path onto
the already-driven prefix of the old one. It simply replaces
`current_route` with the fresh real route `evaluate_route_decision()`
returned (which already starts at the vehicle's current position) and
resets the timing anchor for it. The vehicle's overall driven history
before the reroute point is not reconstructed into one polyline. This is
a documented, acceptable simplification for a prototype demo — the vehicle
is still always on a real graph edge, always following real geometry, and
the reroute is always a real alternative path, never fabricated.
"""
from datetime import datetime, timezone
from typing import Optional

import networkx as nx

from app.config import SIMULATION_SPEED_KMPH
from app.core.geo import interpolate_along_path
from app.core.hazard_state import SegmentHazardContext
from app.core.reroute_service import evaluate_route_decision
from app.core.routing_engine import (
    Location,
    build_remaining_route,
    calculate_route,
    compare_fastest_and_safe_routes,
    compute_route_risk_profile,
)
from app.models.network import Node, RoadSegment
from app.models.route import Route, RouteDecisionOutcome, RouteSafetyOutcome
from app.models.vehicle import Vehicle, VehicleStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _segment_index_for_distance(route: Route, segments_by_id: dict, distance_km: float) -> int:
    """Which segment (by index into route.segment_ids) a given distance
    along the route currently falls in — the last index if distance_km is
    at/beyond the route's total length."""
    cumulative = 0.0
    for i, sid in enumerate(route.segment_ids):
        cumulative += segments_by_id[sid].distance_km
        if distance_km < cumulative or i == len(route.segment_ids) - 1:
            return i
    return max(0, len(route.segment_ids) - 1)


def _position_on_route(route: Route, distance_km: float) -> tuple[float, float]:
    points = [(p.lat, p.lng) for p in route.geometry]
    return interpolate_along_path(points, distance_km)


def _initial_route_for(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
    mode: str = "risk-aware",
):
    """Shared by create_vehicle()/reset_vehicle(): a real route (Part 3/6,
    unchanged) — never a fabricated one. Returns (route, route_risk,
    status, reason).

    mode="risk-aware" (default): the safer of fastest/risk-aware (Part 6) —
    matches every other risk-aware surface in this app; a vehicle created
    this way starts `suspended` if no safe route exists at all.
    mode="fastest": travel-time-only route (Part 3) — route_risk is still
    computed and reported (informational), it just didn't influence which
    path was chosen; such a vehicle always starts `idle`, even if its
    fastest route happens to contain a hard-unsafe segment (in-flight
    hazard response still applies once it's moving — see advance_vehicle()).
    """
    if mode == "fastest":
        route = calculate_route(graph, nodes, segments, origin, destination)
        route_risk = compute_route_risk_profile(route, segments)
        return route, route_risk, VehicleStatus.idle, "Initial route computed (fastest mode); not started yet."

    comparison = compare_fastest_and_safe_routes(graph, nodes, segments, origin, destination)
    if comparison.outcome == RouteSafetyOutcome.no_safe_route_available:
        return (
            comparison.fastest_route,
            comparison.fastest_route_risk,
            VehicleStatus.suspended,
            "No safe route is currently available for this origin/destination.",
        )
    return (
        comparison.recommended_route,
        comparison.recommended_route_risk,
        VehicleStatus.idle,
        "Initial route computed; vehicle has not been started yet.",
    )


def create_vehicle(
    name: str,
    origin: Location,
    destination: Location,
    nodes: list[Node],
    segments: list[RoadSegment],
    graph: nx.DiGraph,
    speed_kmph: float = SIMULATION_SPEED_KMPH,
    mode: str = "risk-aware",
) -> Vehicle:
    """Resolves a REAL initial route (raises UnknownLocationError/
    NoRouteFoundError exactly like the routing endpoints for a bad
    location/genuinely disconnected pair — the API layer maps these to
    400s) and places the vehicle at its very start, unstarted (`idle`).
    See _initial_route_for() for what `mode` controls."""
    route, route_risk, status, reason = _initial_route_for(graph, nodes, segments, origin, destination, mode=mode)
    lat, lng = _position_on_route(route, 0.0)

    return Vehicle(
        name=name,
        origin=route.origin,
        destination=route.destination,
        current_route=route,
        current_segment_id=route.segment_ids[0] if route.segment_ids else None,
        current_lat=lat,
        current_lng=lng,
        progress=0.0,
        distance_travelled_km=0.0,
        distance_remaining_km=route.total_distance_km,
        eta_minutes=round((route.total_distance_km / speed_kmph) * 60, 1) if speed_kmph > 0 else None,
        speed_kmph=speed_kmph,
        status=status,
        route_risk=route_risk,
        last_decision_reason=reason,
    )


def start_vehicle(vehicle: Vehicle) -> Vehicle:
    """Arms the timing anchor and un-pauses. Does not force `en_route` if
    the vehicle is currently `suspended` or `arrived` — the next
    advance_vehicle() call determines the real status from current
    feasibility/position; this only makes sure the clock is running."""
    now = _now()
    if vehicle.status == VehicleStatus.arrived:
        return vehicle

    if vehicle.paused_since is not None:
        vehicle.paused_seconds_total += (now - vehicle.paused_since).total_seconds()
        vehicle.paused_since = None
    vehicle.paused = False

    if vehicle.route_started_at is None:
        vehicle.route_started_at = now
    if vehicle.status == VehicleStatus.idle:
        vehicle.status = VehicleStatus.en_route

    vehicle.updated_at = now
    return vehicle


def pause_vehicle(vehicle: Vehicle) -> Vehicle:
    now = _now()
    if vehicle.status in (VehicleStatus.en_route, VehicleStatus.rerouting) and not vehicle.paused:
        vehicle.paused = True
        vehicle.paused_since = now
    vehicle.updated_at = now
    return vehicle


def reset_vehicle(vehicle: Vehicle, nodes: list[Node], segments: list[RoadSegment], graph: nx.DiGraph) -> Vehicle:
    """Recomputes a fresh initial route from the vehicle's ORIGINAL
    origin/destination (hazards may have changed since it was first
    created) and zeroes all progress/timing — a deterministic full reset
    for repeatable demos."""
    route, route_risk, status, reason = _initial_route_for(graph, nodes, segments, vehicle.origin, vehicle.destination)
    lat, lng = _position_on_route(route, 0.0)

    vehicle.current_route = route
    vehicle.current_segment_id = route.segment_ids[0] if route.segment_ids else None
    vehicle.current_lat = lat
    vehicle.current_lng = lng
    vehicle.progress = 0.0
    vehicle.distance_travelled_km = 0.0
    vehicle.distance_remaining_km = route.total_distance_km
    vehicle.eta_minutes = round((route.total_distance_km / vehicle.speed_kmph) * 60, 1) if vehicle.speed_kmph > 0 else None
    vehicle.status = status
    vehicle.paused = False
    vehicle.route_risk = route_risk
    vehicle.last_decision_reason = reason
    vehicle.route_started_at = None
    vehicle.paused_since = None
    vehicle.paused_seconds_total = 0.0
    vehicle.updated_at = _now()
    return vehicle


def advance_vehicle(
    vehicle: Vehicle,
    nodes: list[Node],
    segments: list[RoadSegment],
    graph: nx.DiGraph,
    segment_context: Optional[dict[str, SegmentHazardContext]] = None,
    active_hazard_ids: Optional[list[str]] = None,
    now: Optional[datetime] = None,
) -> Vehicle:
    """The Part 9 "tick" — a pure recompute-to-`now`, safe to call on every
    poll. No-ops for idle/arrived vehicles and while paused (frozen)."""
    now = now or _now()
    segment_context = segment_context or {}
    active_hazard_ids = active_hazard_ids or []

    if vehicle.status == VehicleStatus.idle or vehicle.status == VehicleStatus.arrived:
        return vehicle
    if vehicle.paused:
        return vehicle
    if vehicle.current_route is None or not vehicle.current_route.segment_ids:
        return vehicle

    segments_by_id = {s.id: s for s in segments}
    route = vehicle.current_route

    # --- Suspended: always re-check (a cleared hazard must be detectable
    #     even when active_hazard_ids is now empty) ---
    if vehicle.status == VehicleStatus.suspended:
        decision = evaluate_route_decision(
            graph, nodes, segments, route.origin, vehicle.destination,
            previous_route=route, segment_context=segment_context, active_hazard_ids=active_hazard_ids,
        )
        vehicle.last_decision_reason = decision.reason
        if decision.outcome == RouteDecisionOutcome.suspend:
            vehicle.updated_at = now
            return vehicle  # still suspended, still frozen
        # Recovered: adopt the (possibly new) recommended route, resume
        # timing fresh, and return immediately -- deliberately NOT falling
        # through to the hazard re-check below, which could otherwise
        # immediately re-run against the very route we just adopted and
        # overwrite the one-tick `rerouting` signal with `en_route` before
        # a poller ever sees it.
        if vehicle.paused_since is not None:
            vehicle.paused_seconds_total += (now - vehicle.paused_since).total_seconds()
            vehicle.paused_since = None
        vehicle.current_route = decision.recommended_route
        vehicle.route_started_at = now
        vehicle.paused_seconds_total = 0.0
        vehicle.status = (
            VehicleStatus.rerouting if decision.outcome == RouteDecisionOutcome.reroute else VehicleStatus.en_route
        )
        route = vehicle.current_route
        lat, lng = _position_on_route(route, 0.0)
        vehicle.current_lat, vehicle.current_lng = lat, lng
        vehicle.current_segment_id = route.segment_ids[0] if route.segment_ids else None
        vehicle.progress = 0.0
        vehicle.distance_travelled_km = 0.0
        vehicle.distance_remaining_km = route.total_distance_km
        vehicle.eta_minutes = (
            round((route.total_distance_km / vehicle.speed_kmph) * 60, 1) if vehicle.speed_kmph > 0 else None
        )
        vehicle.updated_at = now
        return vehicle

    # --- Real elapsed time -> real distance along the CURRENT route ---
    if vehicle.route_started_at is None:
        vehicle.route_started_at = now
    elapsed_seconds = (now - vehicle.route_started_at).total_seconds() - vehicle.paused_seconds_total
    elapsed_hours = max(0.0, elapsed_seconds) / 3600.0
    distance_into_route_km = min(route.total_distance_km, elapsed_hours * vehicle.speed_kmph)

    if route.total_distance_km <= 0 or distance_into_route_km >= route.total_distance_km:
        # Arrived.
        vehicle.status = VehicleStatus.arrived
        vehicle.progress = 1.0
        vehicle.distance_travelled_km = route.total_distance_km
        vehicle.distance_remaining_km = 0.0
        vehicle.eta_minutes = 0.0
        vehicle.current_lat, vehicle.current_lng = _position_on_route(route, route.total_distance_km)
        vehicle.current_segment_id = route.segment_ids[-1]
        vehicle.updated_at = now
        return vehicle

    segment_index = _segment_index_for_distance(route, segments_by_id, distance_into_route_km)

    # --- Hazard awareness: only re-check the routing engine when something
    #     active could plausibly matter, and only against what's AHEAD ---
    just_rerouted = False
    if active_hazard_ids:
        remaining_ids = set(route.segment_ids[segment_index:])
        if remaining_ids & segment_context.keys():
            remaining_route = build_remaining_route(route, segments, segment_index)
            decision = evaluate_route_decision(
                graph, nodes, segments, remaining_route.origin, vehicle.destination,
                previous_route=remaining_route, segment_context=segment_context, active_hazard_ids=active_hazard_ids,
            )
            vehicle.last_decision_reason = decision.reason
            if decision.outcome == RouteDecisionOutcome.suspend:
                vehicle.status = VehicleStatus.suspended
                vehicle.paused_since = now  # freeze the clock while suspended
                vehicle.current_lat, vehicle.current_lng = _position_on_route(route, distance_into_route_km)
                vehicle.current_segment_id = route.segment_ids[segment_index]
                vehicle.progress = round(distance_into_route_km / route.total_distance_km, 4)
                vehicle.distance_travelled_km = round(distance_into_route_km, 3)
                vehicle.distance_remaining_km = round(route.total_distance_km - distance_into_route_km, 3)
                vehicle.updated_at = now
                return vehicle
            if decision.outcome == RouteDecisionOutcome.reroute:
                # Replace the route with the fresh real alternative (see
                # module docstring "Reroute handling") and restart timing
                # from this exact instant -- the new route already begins
                # at the vehicle's current position.
                vehicle.current_route = decision.recommended_route
                vehicle.route_started_at = now
                vehicle.paused_seconds_total = 0.0
                vehicle.status = VehicleStatus.rerouting
                just_rerouted = True
                route = vehicle.current_route
                distance_into_route_km = 0.0
                segment_index = 0
            else:
                vehicle.status = VehicleStatus.en_route

    # A reroute reports `rerouting` for exactly this one tick (see module
    # docstring); every other case settles back to `en_route`.
    if not just_rerouted:
        vehicle.status = VehicleStatus.en_route

    vehicle.current_lat, vehicle.current_lng = _position_on_route(route, distance_into_route_km)
    vehicle.current_segment_id = route.segment_ids[segment_index]
    vehicle.progress = round(distance_into_route_km / route.total_distance_km, 4) if route.total_distance_km else 1.0
    vehicle.distance_travelled_km = round(distance_into_route_km, 3)
    vehicle.distance_remaining_km = round(route.total_distance_km - distance_into_route_km, 3)
    vehicle.eta_minutes = (
        round((vehicle.distance_remaining_km / vehicle.speed_kmph) * 60, 1) if vehicle.speed_kmph > 0 else None
    )
    vehicle.updated_at = now
    return vehicle
