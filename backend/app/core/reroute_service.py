"""
Decides CONTINUE / REROUTE / SUSPEND for an origin/destination given the
current dynamic hazard state (Part 8).

This module computes NO new risk or routing cost of its own -- it is a thin
decision/hysteresis layer entirely on top of Part 6's
compare_fastest_and_safe_routes() and compute_route_risk_profile()
(core/routing_engine.py), which themselves call the unmodified Part 5
risk engine (core/risk_engine.py). Reusing those means "what counts as
unsafe" and "what the recommended route is" are defined in exactly one
place; this module only adds the notion of a PREVIOUS route to be sticky
about, and a hysteresis margin so it isn't sticky to a fault.

--- CONTINUE / REROUTE / SUSPEND, precisely ---

Given `previous_route` (the route currently in effect for this
origin/destination -- None if there isn't one yet, e.g. a first
calculation) and the current risk-aware comparison:

  SUSPEND: no feasible route avoids every hard-unsafe segment (Part 6's
  CASE C -- compare_fastest_and_safe_routes() reported
  no_safe_route_available). recommended_route is None; no replacement
  route is ever fabricated.

  CONTINUE: either (a) there is no previous_route to compare against, and
  a safe recommendation exists -- accepted as-is; or (b) previous_route is
  itself still feasible (no hard-unsafe/closed segment) and the best real
  alternative isn't MEANINGFULLY better (see hysteresis below) -- so we
  deliberately keep recommending previous_route rather than swap for a
  marginal gain; or (c) previous_route already IS the best route.

  REROUTE: previous_route is no longer acceptable (it now contains a
  hard-unsafe or hazard-closed segment) OR a real alternative route is
  MEANINGFULLY safer -- and it differs from previous_route.
  recommended_route is then a real, already-computed alternative (never
  invented) with its own risk profile.

--- Hysteresis (Part 8 section 7) ---

Simple by design: when previous_route is still feasible, we only switch
away from it if the alternative's aggregate_risk_score is at least
ROUTE_CHANGE_HYSTERESIS_SCORE lower (app/config.py -- currently 0.05). This
is the entire mechanism -- no time-windowing, no debounce timers, no
optimization framework. It exists purely to stop the recommendation from
flapping between two routes whose risk happens to sit close to each other
near a boundary; it does NOT apply when previous_route has become
infeasible (a hard-unsafe/closed segment always forces a reroute
regardless of margin -- safety overrides stickiness).
"""
from typing import Optional

import networkx as nx

from app.config import HARD_UNSAFE_RISK_THRESHOLD, RISK_WEIGHT, ROUTE_CHANGE_HYSTERESIS_SCORE
from app.core.hazard_state import SegmentHazardContext
from app.core.routing_engine import Location, compare_fastest_and_safe_routes, compute_route_risk_profile
from app.models.network import Node, RoadSegment
from app.models.route import Route, RouteDecision, RouteDecisionOutcome, RouteSafetyOutcome


def evaluate_route_decision(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
    previous_route: Optional[Route] = None,
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    segment_context: dict[str, SegmentHazardContext] | None = None,
    risk_weight: float = RISK_WEIGHT,
    unsafe_threshold: float = HARD_UNSAFE_RISK_THRESHOLD,
    hysteresis_margin: float = ROUTE_CHANGE_HYSTERESIS_SCORE,
    active_hazard_ids: Optional[list[str]] = None,
) -> RouteDecision:
    """
    The Part 8 entry point. Raises UnknownLocationError/NoRouteFoundError
    exactly like compare_fastest_and_safe_routes() for a bad
    location/genuinely disconnected pair -- SUSPEND is a structured result
    for "connected but no safe path," not for that.
    """
    comparison = compare_fastest_and_safe_routes(
        graph, nodes, segments, origin, destination,
        weather_factor=weather_factor, incident_factor=incident_factor,
        risk_weight=risk_weight, unsafe_threshold=unsafe_threshold, segment_context=segment_context,
    )
    affected_segment_ids = sorted((segment_context or {}).keys())
    hazard_ids = list(active_hazard_ids or [])

    if comparison.outcome == RouteSafetyOutcome.no_safe_route_available:
        return RouteDecision(
            outcome=RouteDecisionOutcome.suspend,
            origin=comparison.fastest_route.origin,
            destination=comparison.fastest_route.destination,
            previous_route=previous_route,
            recommended_route=None,
            previous_route_risk=None,
            recommended_route_risk=None,
            affected_segment_ids=affected_segment_ids,
            active_hazard_ids=hazard_ids,
            eta_change_min=None,
            reason=(
                "No feasible route avoids every segment at/above the hard unsafe risk "
                "threshold (or operationally closed by an active simulated hazard) -- "
                "dispatch suspended. No replacement route exists to recommend."
            ),
        )

    best_route = comparison.recommended_route
    best_risk = comparison.recommended_route_risk or comparison.fastest_route_risk

    if previous_route is None:
        return RouteDecision(
            outcome=RouteDecisionOutcome.continue_,
            origin=best_route.origin,
            destination=best_route.destination,
            previous_route=None,
            recommended_route=best_route,
            previous_route_risk=None,
            recommended_route_risk=best_risk,
            affected_segment_ids=affected_segment_ids,
            active_hazard_ids=hazard_ids,
            eta_change_min=None,
            reason="No previous route to compare against -- the current risk-aware recommendation is accepted.",
        )

    if previous_route.node_ids == best_route.node_ids:
        return RouteDecision(
            outcome=RouteDecisionOutcome.continue_,
            origin=previous_route.origin,
            destination=previous_route.destination,
            previous_route=previous_route,
            recommended_route=previous_route,
            previous_route_risk=best_risk,
            recommended_route_risk=best_risk,
            affected_segment_ids=affected_segment_ids,
            active_hazard_ids=hazard_ids,
            eta_change_min=0.0,
            reason="The recommended route is unchanged from the previous route.",
        )

    previous_route_risk = compute_route_risk_profile(
        previous_route, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        unsafe_threshold=unsafe_threshold, segment_context=segment_context,
    )
    previous_is_infeasible = previous_route_risk.unsafe_segment_count > 0

    if not previous_is_infeasible:
        improvement = previous_route_risk.aggregate_risk_score - best_risk.aggregate_risk_score
        if improvement < hysteresis_margin:
            return RouteDecision(
                outcome=RouteDecisionOutcome.continue_,
                origin=previous_route.origin,
                destination=previous_route.destination,
                previous_route=previous_route,
                recommended_route=previous_route,
                previous_route_risk=previous_route_risk,
                recommended_route_risk=previous_route_risk,
                affected_segment_ids=affected_segment_ids,
                active_hazard_ids=hazard_ids,
                eta_change_min=0.0,
                reason=(
                    f"Previous route remains feasible and the real alternative is not "
                    f"meaningfully safer (improvement {improvement:.2f} < hysteresis margin "
                    f"{hysteresis_margin}) -- keeping the current route to avoid flapping."
                ),
            )

    eta_change_min = round(best_route.estimated_travel_time_min - previous_route.estimated_travel_time_min, 1)
    reason = (
        "Previous route now contains a segment at/above the hard unsafe risk threshold "
        "(or operationally closed by an active simulated hazard) -- rerouting to a real, "
        "already-verified alternative."
        if previous_is_infeasible
        else (
            f"A real alternative route is meaningfully safer (prototype risk "
            f"{previous_route_risk.aggregate_risk_score:.2f} -> {best_risk.aggregate_risk_score:.2f}) "
            "-- rerouting."
        )
    )
    return RouteDecision(
        outcome=RouteDecisionOutcome.reroute,
        origin=previous_route.origin,
        destination=previous_route.destination,
        previous_route=previous_route,
        recommended_route=best_route,
        previous_route_risk=previous_route_risk,
        recommended_route_risk=best_risk,
        affected_segment_ids=affected_segment_ids,
        active_hazard_ids=hazard_ids,
        eta_change_min=eta_change_min,
        reason=reason,
    )
