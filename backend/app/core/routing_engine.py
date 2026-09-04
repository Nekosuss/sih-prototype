"""
Wraps the road network in a networkx graph and computes routes.

Part 3 scope: a clean baseline routing engine. The optimization objective is
travel time only — no hazard/risk cost yet (that is Part 4+ scope). The
routing cost is isolated in edge_cost() specifically so that later work can
change what "cost" means (cost = travel_time + risk_weight * hazard_cost)
without touching graph construction, Dijkstra, or route/geometry assembly.

This module has no dependency on FastAPI, React, weather, or vehicles — it
can be exercised directly:

    nodes, segments = load_network()
    graph = build_graph(nodes, segments)
    route = calculate_route(graph, nodes, segments, "Guwahati", "Tawang")
"""
import networkx as nx

from app.core.geo import haversine_km
from app.models.network import GeoPoint, Node, RoadSegment
from app.models.route import Route

Location = str | GeoPoint  # a known node_id/name, or raw coordinates


class UnknownLocationError(ValueError):
    """Raised when a location string doesn't match any known node id/name."""


class NoRouteFoundError(ValueError):
    """Raised when origin and destination exist but no path connects them."""


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


def calculate_route(
    graph: nx.DiGraph,
    nodes: list[Node],
    segments: list[RoadSegment],
    origin: Location,
    destination: Location,
) -> Route:
    """
    Resolve origin/destination, find the lowest-edge_cost path (Dijkstra, via
    networkx), and assemble it into a Route: ordered nodes, ordered segments,
    real totals (distance/time, always from the actual segment data
    regardless of the cost function used to choose the path), and one
    continuous geometry polyline built from the real road geometry of each
    segment traversed, in the correct direction.
    """
    origin_node = resolve_location(nodes, origin)
    destination_node = resolve_location(nodes, destination)

    try:
        node_path = nx.shortest_path(graph, origin_node.id, destination_node.id, weight=_weight_fn)
    except nx.NetworkXNoPath as exc:
        raise NoRouteFoundError(
            f"No route exists between {origin_node.id!r} and {destination_node.id!r}"
        ) from exc

    segments_by_id = {s.id: s for s in segments}

    segment_ids: list[str] = []
    geometry_pieces: list[list[GeoPoint]] = []
    total_distance_km = 0.0
    total_travel_time_min = 0.0

    for u, v in zip(node_path, node_path[1:]):
        edge_data = graph[u][v]
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
