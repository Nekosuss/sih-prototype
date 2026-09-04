"""
Wraps the road network in a networkx graph and computes routes.

Two generations of routing live here side by side:

1. BASELINE (Part 3, unchanged by Part 6/8): build_graph()/edge_cost()/
   calculate_route(). Cost = travel time only, no hazard/risk. Every
   existing caller/test of these keeps working exactly as before.

2. RISK-AWARE ROUTING (Part 6, extended by Part 8): build_risk_aware_graph()/
   risk_aware_edge_cost()/calculate_risk_aware_route()/
   compute_route_risk_profile()/compare_fastest_and_safe_routes(). Cost
   also factors in each segment's Part 5 explainable prototype risk_score
   (core/risk_engine.py), and any segment at/above a hard threshold is
   excluded from the graph entirely rather than merely penalized. See that
   section's docstring for the exact formula/threshold/aggregation and why
   each choice was made.

   Part 8 adds an optional `segment_context` parameter (a
   dict[segment_id, hazard_state.SegmentHazardContext]) threaded through
   every function in that section: a PER-SEGMENT override of
   weather_factor/incident_factor/closed, layered on top of the existing
   UNIFORM weather_factor/incident_factor params from Part 6. Omitting it
   (the default, None) reproduces Part 6's exact prior behavior — this is
   a purely additive extension, not a rewrite of the risk-aware routing
   logic. See core/hazard_state.py for how simulated hazard events become
   a segment_context dict.

This module has no dependency on FastAPI, React, weather, or vehicles — it
can be exercised directly:

    nodes, segments = load_network()
    graph = build_graph(nodes, segments)
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")
    result = compare_fastest_and_safe_routes(graph, nodes, segments, "Guwahati", "Tawang")

Part 9 (app/simulation/vehicle_simulator.py) additionally uses
build_remaining_route() (below) to get the unfinished portion of an
already-assigned route, and reuses calculate_route()/
compare_fastest_and_safe_routes() unchanged for a vehicle's initial route.
"""
import networkx as nx

from app.config import HARD_UNSAFE_RISK_THRESHOLD, RISK_WEIGHT, ROUTE_AGGREGATE_MAX_WEIGHT
from app.core.geo import haversine_km
from app.core.hazard_state import SegmentHazardContext
from app.core.risk_engine import assess_segment_risk
from app.models.network import GeoPoint, Node, RoadSegment
from app.models.risk import RiskLevel
from app.models.route import Route, RouteRiskProfile, RouteSafetyOutcome, RiskAwareRouteResult

Location = str | GeoPoint  # a known node_id/name, or raw coordinates


class UnknownLocationError(ValueError):
    """Raised when a location string doesn't match any known node id/name."""


class NoRouteFoundError(ValueError):
    """Raised when origin and destination exist but no path connects them."""


class NoSafeRouteFoundError(NoRouteFoundError):
    """
    Raised by calculate_risk_aware_route() specifically when origin and
    destination ARE connected in the ordinary (all-edges) road graph, but
    no path avoids every segment at/above HARD_UNSAFE_RISK_THRESHOLD — i.e.
    a road physically exists, but every option is blocked by risk, not by
    disconnection. Subclasses NoRouteFoundError so existing "no route"
    handling still degrades sensibly; catch this specifically to
    distinguish "genuinely no road" from "a road exists but every option is
    unsafe" (Part 6 section 5, CASE C).
    """


def build_graph(nodes: list[Node], segments: list[RoadSegment]) -> nx.DiGraph:
    """
    Builds a directed graph so oneway roads can be represented correctly:
    every segment gets a from_node_id -> to_node_id edge, and an additional
    reverse edge only if segment.bidirectional is True (i.e. the OSM
    `oneway` tag was not exactly "yes" — see osm_geojson_loader.py). A
    two-way road ends up with two directed edges carrying the same
    attributes, which is functionally equivalent to the undirected graph
    this used to be for a dataset with no one-way roads (e.g. the
    synthetic test graphs in tests/test_routing.py) — nx.shortest_path,
    nx.has_path, etc. all work the same way on a DiGraph.
    """
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node.id, **node.model_dump())
    for segment in segments:
        edge_attrs = dict(
            segment_id=segment.id,
            distance_km=segment.distance_km,
            travel_time_min=segment.estimated_travel_time_min,
            weight=segment.distance_km,
            risk=segment.current_risk_score,
        )
        graph.add_edge(segment.from_node_id, segment.to_node_id, **edge_attrs)
        if segment.bidirectional:
            graph.add_edge(segment.to_node_id, segment.from_node_id, **edge_attrs)
    return graph


def shortest_path_by_distance(graph: nx.DiGraph, origin_id: str, destination_id: str) -> list[str]:
    """Plain shortest path by distance_km. Superseded for route calculation by
    calculate_route() below (which uses edge_cost/travel time); kept as-is
    since Part 2 tests exercise it directly."""
    return nx.shortest_path(graph, origin_id, destination_id, weight="distance_km")


def edge_cost(edge_data: dict) -> float:
    """
    THE single seam for routing cost. Everything else (graph construction,
    Dijkstra, route assembly) only ever consumes this function's return
    value — never distance_km or travel_time_min directly for pathfinding.

    Part 3 baseline:  cost = travel_time
    Future (Part 4+): cost = travel_time + risk_weight * hazard_cost

    Changing the formula later means editing this function only.
    """
    return edge_data["travel_time_min"]


def _weight_fn(_u: str, _v: str, edge_data: dict) -> float:
    return edge_cost(edge_data)


def resolve_location(nodes: list[Node], location: Location) -> Node:
    """
    Resolve a Location into a graph Node:
      - str: matched against node id, then node name (case-insensitive)
      - GeoPoint: matched to the nearest node by great-circle distance

    Raises UnknownLocationError if a string doesn't match anything. Never
    calls an external service — this is purely local lookup over the graph's
    own nodes.
    """
    if isinstance(location, GeoPoint):
        return min(nodes, key=lambda n: haversine_km(location.lat, location.lng, n.lat, n.lng))

    needle = location.strip().lower()
    for node in nodes:
        if node.id.lower() == needle:
            return node
    for node in nodes:
        if node.name and node.name.lower() == needle:
            return node
    raise UnknownLocationError(f"Unknown location: {location!r}")


def has_alternative_path(graph: nx.DiGraph, origin_id: str, destination_id: str) -> bool:
    """
    True if, after removing the edges of the current shortest path, some
    other path still connects origin and destination — i.e. whether this
    graph actually has enough branching for a meaningful alternative-route
    comparison. Used to decide whether to expose alternatives rather than
    fabricating them (see ARCHITECTURE notes in routes_routing.py).
    """
    path = nx.shortest_path(graph, origin_id, destination_id, weight=_weight_fn)
    edges_to_remove = list(zip(path, path[1:]))
    reduced = graph.copy()
    reduced.remove_edges_from(edges_to_remove)
    return nx.has_path(reduced, origin_id, destination_id)


def _segment_geometry_in_direction(segment: RoadSegment, traveling_from: str) -> list[GeoPoint]:
    if segment.from_node_id == traveling_from:
        return list(segment.geometry)
    return list(reversed(segment.geometry))


def _combine_geometry(pieces: list[list[GeoPoint]]) -> list[GeoPoint]:
    """Concatenate ordered per-segment geometries into one continuous
    polyline, dropping an exact-duplicate point at a shared junction."""
    combined: list[GeoPoint] = []
    for piece in pieces:
        if combined and combined[-1] == piece[0]:
            combined.extend(piece[1:])
        else:
            combined.extend(piece)
    return combined


def _assemble_route(
    path_graph: nx.DiGraph,
    node_path: list[str],
    segments: list[RoadSegment],
    origin_node: Node,
    destination_node: Node,
) -> Route:
    """
    Shared by calculate_route() and calculate_risk_aware_route(): turns an
    already-found node path (from whichever graph/cost function located it)
    into a Route — ordered segments, real totals (distance/time, always
    from the actual segment data regardless of the cost function used to
    choose the path), and one continuous geometry polyline built from the
    real road geometry of each segment traversed, in the correct direction.
    Pure assembly, no pathfinding — extracted so both routing modes build
    identical, consistent Route objects from a node path.
    """
    segments_by_id = {s.id: s for s in segments}

    segment_ids: list[str] = []
    geometry_pieces: list[list[GeoPoint]] = []
    total_distance_km = 0.0
    total_travel_time_min = 0.0

    for u, v in zip(node_path, node_path[1:]):
        edge_data = path_graph[u][v]
        segment = segments_by_id[edge_data["segment_id"]]

        segment_ids.append(segment.id)
        geometry_pieces.append(_segment_geometry_in_direction(segment, traveling_from=u))
        total_distance_km += segment.distance_km
        total_travel_time_min += segment.estimated_travel_time_min

    return Route(
        origin=origin_node.id,
        destination=destination_node.id,
        node_ids=node_path,
        segment_ids=segment_ids,
        total_distance_km=round(total_distance_km, 2),
        estimated_travel_time_min=round(total_travel_time_min, 1),
        geometry=_combine_geometry(geometry_pieces),
    )


def build_remaining_route(route: Route, segments: list[RoadSegment], from_segment_index: int) -> Route:
    """
    Part 9 (vehicle simulation): returns a new Route covering only the
    UNFINISHED portion of `route`, starting at `from_segment_index` (0 =
    the whole route unchanged). Used so a moving vehicle's hazard/reroute
    re-check (core/reroute_service.py::evaluate_route_decision) only
    considers what's still AHEAD of it — a hazard on a segment already
    safely passed must never trigger a reroute or count towards
    infeasibility. Built from the real segment geometry the exact same way
    _assemble_route() does (same direction-correction helper), so if this
    is ever displayed it's honest, real geometry — not a placeholder.

    `from_segment_index >= len(route.segment_ids)` (vehicle already at/past
    the last segment) returns a degenerate zero-length Route at the
    destination — a real, if empty, route rather than raising.
    """
    segments_by_id = {s.id: s for s in segments}
    node_ids = route.node_ids[from_segment_index:]
    segment_ids = route.segment_ids[from_segment_index:]

    if not segment_ids:
        return Route(
            origin=route.destination,
            destination=route.destination,
            node_ids=[route.destination],
            segment_ids=[],
            total_distance_km=0.0,
            estimated_travel_time_min=0.0,
            geometry=[],
        )

    geometry_pieces: list[list[GeoPoint]] = []
    total_distance_km = 0.0
    total_travel_time_min = 0.0
    for i, segment_id in enumerate(segment_ids):
        segment = segments_by_id[segment_id]
        geometry_pieces.append(_segment_geometry_in_direction(segment, traveling_from=node_ids[i]))
        total_distance_km += segment.distance_km
        total_travel_time_min += segment.estimated_travel_time_min

    return Route(
        origin=node_ids[0],
        destination=route.destination,
        node_ids=node_ids,
        segment_ids=segment_ids,
        total_distance_km=round(total_distance_km, 2),
        estimated_travel_time_min=round(total_travel_time_min, 1),
        geometry=_combine_geometry(geometry_pieces),
    )


def calculate_route(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
) -> Route:
    """
    Resolve origin/destination, find the lowest-edge_cost path (Dijkstra, via
    networkx), and assemble it into a Route. Baseline (Part 3): cost is
    travel time only — see edge_cost(). Unchanged by Part 6.
    """
    origin_node = resolve_location(nodes, origin)
    destination_node = resolve_location(nodes, destination)

    try:
        node_path = nx.shortest_path(graph, origin_node.id, destination_node.id, weight=_weight_fn)
    except nx.NetworkXNoPath as exc:
        raise NoRouteFoundError(
            f"No route exists between {origin_node.id!r} and {destination_node.id!r}"
        ) from exc

    return _assemble_route(graph, node_path, segments, origin_node, destination_node)


# ---------------------------------------------------------------------------
# Part 6: risk-aware routing.
#
# Builds on Part 5's per-segment assess_segment_risk() (core/risk_engine.py)
# without that module ever importing anything from here — risk_engine.py
# stays a pure segment-level scorer with zero routing knowledge; the
# dependency runs one way, routing_engine -> risk_engine, same as
# routing_engine already depends on core/geo.py.
#
# --- Combined cost formula ---
#
#   risk_aware_edge_cost = travel_time_min * (1 + RISK_WEIGHT * risk_score)
#
# Multiplicative, not `travel_time_min + RISK_WEIGHT * risk_score`: an
# additive term would need its own arbitrary "how many minutes is 1.0 risk
# worth" conversion constant just to stop a 0-1 score from being drowned
# out by minutes, and it would charge the same flat risk penalty to a
# 200m risky segment as a 20km one despite wildly different real exposure.
# Scaling travel_time_min itself means the penalty is proportional to how
# long you're actually on that segment, needs only one weight
# (RISK_WEIGHT, app/config.py), and reduces to exactly the Part 3 baseline
# cost when risk_score is 0. See app/config.py for the exact value and
# reasoning.
#
# --- Hard unsafe threshold ---
#
# A segment with risk_score >= HARD_UNSAFE_RISK_THRESHOLD (app/config.py,
# currently == the Part 5 risk engine's own "critical" threshold) is not
# merely made expensive — build_risk_aware_graph() never adds its edge to
# the graph at all, in either direction. Pathfinding on that graph
# therefore cannot select it under any circumstances, no matter how much
# time it would save.
#
# --- Route risk aggregation ---
#
# compute_route_risk_profile() does NOT average segment risk scores — a
# long, mostly-safe route with one dangerous segment must not read as
# "low risk" just because it's diluted across many safe segments. Instead:
#
#   aggregate_risk_score = ROUTE_AGGREGATE_MAX_WEIGHT * max_segment_risk
#                         + (1 - ROUTE_AGGREGATE_MAX_WEIGHT) * mean_segment_risk
#
# weighted towards the maximum (app/config.py). max_segment_risk and the
# count of segments at each RiskLevel are always reported alongside the
# aggregate, precisely so a caller never has to trust one blended number
# alone to know "is there a single bad spot on this route."
#
# --- What this IS and ISN'T ---
#
# This is an explainable PROTOTYPE risk-aware routing system: every cost,
# threshold, and aggregation step above is a documented, auditable formula
# over real data (DEM slope, GSI-matched landslide history — see Part 4.8/
# risk_engine.py) plus optional externally-supplied weather/incident
# context. It is NOT a trained ML route-prediction system, and risk_score
# is NOT a calibrated probability of a landslide or disruption occurring —
# see risk_engine.py and training_dataset_schema.md for exactly why that
# calibration doesn't exist yet. Route selection still only ever chooses
# among paths that ACTUALLY EXIST in the real road graph — this module
# never fabricates or invents an alternative route; compare_fastest_and_-
# safe_routes() below only ever runs Dijkstra over graphs built from the
# real segments passed in.
# ---------------------------------------------------------------------------


def risk_aware_edge_cost(edge_data: dict, risk_weight: float = RISK_WEIGHT) -> float:
    """THE single seam for risk-aware routing cost — mirrors edge_cost()'s
    role for baseline routing. edge_data must carry a `risk_score` key (see
    build_risk_aware_graph(), which is the only place that adds one)."""
    return edge_data["travel_time_min"] * (1.0 + risk_weight * edge_data["risk_score"])


def _resolve_segment_factors(
    segment_id: str,
    weather_factor: float | None,
    incident_factor: float | None,
    segment_context: dict[str, SegmentHazardContext] | None,
) -> tuple[float | None, float | None, bool]:
    """Part 8: a per-segment SegmentHazardContext (from an active simulated
    hazard, see core/hazard_state.py) overrides the UNIFORM
    weather_factor/incident_factor for that one segment only; every other
    segment keeps using the uniform values unchanged. Returns
    (effective_weather_factor, effective_incident_factor, closed)."""
    ctx = (segment_context or {}).get(segment_id)
    if ctx is None:
        return weather_factor, incident_factor, False
    effective_weather = ctx.weather_factor if ctx.weather_factor is not None else weather_factor
    effective_incident = ctx.incident_factor if ctx.incident_factor is not None else incident_factor
    return effective_weather, effective_incident, ctx.closed


def build_risk_aware_graph(
    nodes: list[Node],
    segments: list[RoadSegment],
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    unsafe_threshold: float = HARD_UNSAFE_RISK_THRESHOLD,
    segment_context: dict[str, SegmentHazardContext] | None = None,
) -> nx.DiGraph:
    """
    Like build_graph(), but every edge also carries the Part 5 explainable
    prototype `risk_score`/`risk_level` (recomputed fresh from each
    segment's real slope/historical-landslide data plus the given
    weather_factor/incident_factor — NOT the old Part 2 `current_risk_score`
    that build_graph() stores), and any segment scoring at/above
    `unsafe_threshold` is excluded from the graph entirely (both directions)
    rather than added with a high cost. See module docstring.

    weather_factor/incident_factor, when given, apply UNIFORMLY to every
    segment in the network for this call — there is no per-segment/
    per-region weather or incident targeting from these two params alone.
    `segment_context` (Part 8) is the per-segment override on top of that
    uniform baseline — see _resolve_segment_factors(). A segment whose
    resolved context has `closed=True` is excluded from the graph exactly
    like a hard-unsafe segment, regardless of its computed risk_score (see
    app/config.py::HAZARD_CLOSURE_TYPES for why that's a deliberate
    bypass of the weighted formula, not a hidden assumption).
    """
    graph = nx.DiGraph()
    for node in nodes:
        graph.add_node(node.id, **node.model_dump())

    for segment in segments:
        seg_weather, seg_incident, closed = _resolve_segment_factors(
            segment.id, weather_factor, incident_factor, segment_context
        )
        result = assess_segment_risk(segment, weather_factor=seg_weather, incident_factor=seg_incident)
        if closed or result.risk_score >= unsafe_threshold:
            continue  # excluded entirely — see module docstring

        edge_attrs = dict(
            segment_id=segment.id,
            distance_km=segment.distance_km,
            travel_time_min=segment.estimated_travel_time_min,
            weight=segment.distance_km,
            risk_score=result.risk_score,
            risk_level=result.risk_level.value,
        )
        graph.add_edge(segment.from_node_id, segment.to_node_id, **edge_attrs)
        if segment.bidirectional:
            graph.add_edge(segment.to_node_id, segment.from_node_id, **edge_attrs)
    return graph


def _make_risk_aware_weight_fn(risk_weight: float):
    def weight_fn(_u: str, _v: str, edge_data: dict) -> float:
        return risk_aware_edge_cost(edge_data, risk_weight)

    return weight_fn


def calculate_risk_aware_route(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    risk_weight: float = RISK_WEIGHT,
    unsafe_threshold: float = HARD_UNSAFE_RISK_THRESHOLD,
    segment_context: dict[str, SegmentHazardContext] | None = None,
) -> Route:
    """
    Like calculate_route(), but pathfinds over build_risk_aware_graph()
    using risk_aware_edge_cost() instead of the plain-travel-time graph.

    `graph` must be the ordinary (all-edges, baseline) ALL-SEGMENTS graph
    for this network (e.g. StateStore.graph) — used ONLY to distinguish
    "genuinely no road connects these points" (raises NoRouteFoundError,
    exactly like calculate_route()) from "a road exists but every path
    is blocked by the hard unsafe threshold" (raises NoSafeRouteFoundError).
    The risk-aware graph itself is built fresh here from `segments`, since
    it depends on weather_factor/incident_factor/unsafe_threshold/
    segment_context, which can vary per call. segment_context (Part 8) is
    the per-segment hazard override — see build_risk_aware_graph().
    """
    origin_node = resolve_location(nodes, origin)
    destination_node = resolve_location(nodes, destination)

    risk_graph = build_risk_aware_graph(
        nodes, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        unsafe_threshold=unsafe_threshold, segment_context=segment_context,
    )
    weight_fn = _make_risk_aware_weight_fn(risk_weight)

    try:
        node_path = nx.shortest_path(risk_graph, origin_node.id, destination_node.id, weight=weight_fn)
    except nx.NetworkXNoPath as exc:
        if nx.has_path(graph, origin_node.id, destination_node.id):
            raise NoSafeRouteFoundError(
                f"No route avoiding segments at/above risk {unsafe_threshold} exists between "
                f"{origin_node.id!r} and {destination_node.id!r}, though a route ignoring risk does exist"
            ) from exc
        raise NoRouteFoundError(
            f"No route exists between {origin_node.id!r} and {destination_node.id!r}"
        ) from exc

    return _assemble_route(risk_graph, node_path, segments, origin_node, destination_node)


def get_route_segment_risks(
    route: Route,
    segments: list[RoadSegment],
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    segment_context: dict[str, SegmentHazardContext] | None = None,
) -> list:
    """
    The Part 5 RiskResult for every segment on `route`, in the same order
    as route.segment_ids. Pure, side-effect-free — reused by
    compute_route_risk_profile() below (so the aggregate and the
    individual per-segment results are always computed from the exact same
    values, never recomputed twice with a chance to drift) and by the API
    layer (routes_routing.py) to expose per-segment detail for the UI
    (e.g. a map hover/click) without duplicating risk logic in JavaScript.

    segment_context (Part 8): per-segment weather/incident override (see
    _resolve_segment_factors()); note a RiskResult never reflects a
    segment's `closed` status — closure is a routing-feasibility concept,
    not a risk-SCORE concept (see compute_route_risk_profile()'s
    unsafe_segment_count for where closure is actually surfaced).
    """
    segments_by_id = {s.id: s for s in segments}
    results = []
    for sid in route.segment_ids:
        seg_weather, seg_incident, _closed = _resolve_segment_factors(sid, weather_factor, incident_factor, segment_context)
        results.append(assess_segment_risk(segments_by_id[sid], weather_factor=seg_weather, incident_factor=seg_incident))
    return results


def compute_route_risk_profile(
    route: Route,
    segments: list[RoadSegment],
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    unsafe_threshold: float = HARD_UNSAFE_RISK_THRESHOLD,
    segment_risks: list | None = None,
    segment_context: dict[str, SegmentHazardContext] | None = None,
) -> RouteRiskProfile:
    """
    Explainable prototype route-level risk profile for an already-computed
    Route (baseline or risk-aware — this function doesn't care which). See
    module docstring for the aggregation formula and why it isn't a plain
    average. weather_factor/incident_factor must match whatever was used
    to select the route being profiled, for the numbers to be consistent
    with each other (both default to None, matching calculate_route()'s
    lack of any weather/incident awareness).

    segment_risks: optionally pass in an already-computed
    get_route_segment_risks() result to avoid recomputing it (used by
    compare_fastest_and_safe_routes()); if omitted, computed internally —
    existing callers/tests are unaffected either way.

    segment_context (Part 8): a segment marked `closed` (see
    core/hazard_state.py) counts toward `unsafe_segment_count` regardless
    of its numeric risk_score — closure means "operationally unavailable",
    which build_risk_aware_graph() already excludes outright, so a route
    profile must not silently call that segment "safe" just because the
    weighted formula alone didn't reach the hard threshold. This does NOT
    inflate max_segment_risk/aggregate_risk_score to a fabricated 1.0 for a
    closed segment — those stay the real formula output; the closure fact
    is surfaced through unsafe_segment_count instead (and, further up the
    stack, through HazardEvent/RouteDecision).
    """
    level_counts = {level.value: 0 for level in RiskLevel}

    if not route.segment_ids:
        return RouteRiskProfile(
            aggregate_risk_score=0.0,
            max_segment_risk=0.0,
            max_risk_segment_id=None,
            segment_count_by_risk_level=level_counts,
            unsafe_segment_count=0,
            hard_unsafe_threshold=unsafe_threshold,
            weather_factor_used=weather_factor,
            incident_factor_used=incident_factor,
        )

    results = (
        segment_risks
        if segment_risks is not None
        else get_route_segment_risks(
            route, segments, weather_factor=weather_factor, incident_factor=incident_factor, segment_context=segment_context
        )
    )
    for result in results:
        level_counts[result.risk_level.value] += 1

    max_result = max(results, key=lambda r: r.risk_score)
    mean_risk = sum(r.risk_score for r in results) / len(results)
    aggregate = ROUTE_AGGREGATE_MAX_WEIGHT * max_result.risk_score + (1 - ROUTE_AGGREGATE_MAX_WEIGHT) * mean_risk

    unsafe_count = 0
    for sid, result in zip(route.segment_ids, results):
        ctx = (segment_context or {}).get(sid)
        is_closed = bool(ctx and ctx.closed)
        if is_closed or result.risk_score >= unsafe_threshold:
            unsafe_count += 1

    return RouteRiskProfile(
        aggregate_risk_score=round(min(1.0, max(0.0, aggregate)), 4),
        max_segment_risk=max_result.risk_score,
        max_risk_segment_id=max_result.segment_id,
        segment_count_by_risk_level=level_counts,
        unsafe_segment_count=unsafe_count,
        hard_unsafe_threshold=unsafe_threshold,
        weather_factor_used=weather_factor,
        incident_factor_used=incident_factor,
    )


def compare_fastest_and_safe_routes(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
    weather_factor: float | None = None,
    incident_factor: float | None = None,
    risk_weight: float = RISK_WEIGHT,
    unsafe_threshold: float = HARD_UNSAFE_RISK_THRESHOLD,
    segment_context: dict[str, SegmentHazardContext] | None = None,
) -> RiskAwareRouteResult:
    """
    Part 6 section 5's route comparison, end to end. Computes the fastest
    (travel-time-only) route AND the risk-aware route for the same
    origin/destination, using the REAL graph/segments only — never
    fabricates a route — and classifies the outcome into exactly one of
    three cases:

      CASE A (fastest_route_is_safe): the risk-aware optimizer picks the
      exact same path as the fastest one — i.e. the fastest route was
      already an acceptable choice under risk-aware cost too.

      CASE B (safer_route_selected): the risk-aware path differs from the
      fastest one (either because the fastest route contains a hard-unsafe
      segment that had to be excluded, or simply because a slightly slower
      path is meaningfully lower-risk under the weighted cost). The
      risk-aware route is the recommendation.

      CASE C (no_safe_route_available): origin and destination are
      connected in the ordinary graph, but every path is blocked by the
      hard unsafe threshold — calculate_risk_aware_route() raised
      NoSafeRouteFoundError. recommended_route/recommended_route_risk are
      None; this is reported structurally, not raised, since it's a
      meaningful outcome for a caller to display, not a programming error.

    Never raises NoSafeRouteFoundError itself; still raises
    UnknownLocationError/NoRouteFoundError for a genuinely bad
    location/disconnected pair, exactly like calculate_route().
    """
    fastest_route = calculate_route(graph, nodes, segments, origin, destination)
    fastest_segment_risks = get_route_segment_risks(
        fastest_route, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        segment_context=segment_context,
    )
    fastest_route_risk = compute_route_risk_profile(
        fastest_route, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        unsafe_threshold=unsafe_threshold, segment_risks=fastest_segment_risks, segment_context=segment_context,
    )
    unsafe_in_fastest = fastest_route_risk.unsafe_segment_count > 0

    try:
        safe_route = calculate_risk_aware_route(
            graph, nodes, segments, origin, destination,
            weather_factor=weather_factor, incident_factor=incident_factor,
            risk_weight=risk_weight, unsafe_threshold=unsafe_threshold, segment_context=segment_context,
        )
    except NoSafeRouteFoundError:
        return RiskAwareRouteResult(
            outcome=RouteSafetyOutcome.no_safe_route_available,
            fastest_route=fastest_route,
            fastest_route_risk=fastest_route_risk,
            recommended_route=None,
            recommended_route_risk=None,
            safer_alternative_selected=False,
            unsafe_segments_in_fastest_route=unsafe_in_fastest,
            fastest_route_segment_risks=fastest_segment_risks,
            recommended_route_segment_risks=None,
            reasons=[
                "The fastest route contains at least one segment at/above the hard unsafe "
                f"risk threshold ({unsafe_threshold}).",
                "No alternative path between these points avoids every such segment in the "
                "real road network — every physically possible route is blocked by risk.",
                "No safe route available: returning the fastest route's risk profile for "
                "visibility only. This is a prototype risk assessment, not a calibrated "
                "probability of disruption.",
            ],
        )

    if safe_route.node_ids == fastest_route.node_ids:
        return RiskAwareRouteResult(
            outcome=RouteSafetyOutcome.fastest_route_is_safe,
            fastest_route=fastest_route,
            fastest_route_risk=fastest_route_risk,
            recommended_route=fastest_route,
            recommended_route_risk=fastest_route_risk,
            safer_alternative_selected=False,
            unsafe_segments_in_fastest_route=unsafe_in_fastest,
            fastest_route_segment_risks=fastest_segment_risks,
            recommended_route_segment_risks=None,  # identical to fastest_route_segment_risks — see model docstring
            reasons=[
                "The fastest route is also the risk-aware choice: no segment on it is at/above "
                f"the hard unsafe risk threshold ({unsafe_threshold}), and no other real path "
                "scores meaningfully lower under the risk-weighted cost.",
                f"Prototype route risk score: {fastest_route_risk.aggregate_risk_score:.2f} "
                "(explainable estimate, not a calibrated probability).",
            ],
        )

    safe_segment_risks = get_route_segment_risks(
        safe_route, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        segment_context=segment_context,
    )
    safe_route_risk = compute_route_risk_profile(
        safe_route, segments, weather_factor=weather_factor, incident_factor=incident_factor,
        unsafe_threshold=unsafe_threshold, segment_risks=safe_segment_risks, segment_context=segment_context,
    )
    reasons = []
    if unsafe_in_fastest:
        reasons.append(
            f"The fastest route contains {fastest_route_risk.unsafe_segment_count} segment(s) at/above "
            f"the hard unsafe risk threshold ({unsafe_threshold}); those are excluded entirely from the "
            "safer route below."
        )
    else:
        reasons.append(
            "No segment on the fastest route is hard-unsafe, but a real alternative path scores "
            "meaningfully lower under the risk-weighted cost."
        )
    reasons.append(
        f"Selected route: prototype aggregate risk {safe_route_risk.aggregate_risk_score:.2f} "
        f"(vs. {fastest_route_risk.aggregate_risk_score:.2f} for the fastest route), "
        f"+{round(safe_route.estimated_travel_time_min - fastest_route.estimated_travel_time_min, 1)} "
        "min travel time versus the fastest route."
    )
    reasons.append("This is an explainable prototype risk assessment, not a calibrated probability of disruption.")

    return RiskAwareRouteResult(
        outcome=RouteSafetyOutcome.safer_route_selected,
        fastest_route=fastest_route,
        fastest_route_risk=fastest_route_risk,
        recommended_route=safe_route,
        recommended_route_risk=safe_route_risk,
        safer_alternative_selected=True,
        unsafe_segments_in_fastest_route=unsafe_in_fastest,
        fastest_route_segment_risks=fastest_segment_risks,
        recommended_route_segment_risks=safe_segment_risks,
        reasons=reasons,
    )
