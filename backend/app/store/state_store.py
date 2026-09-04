"""
Single in-memory source of truth.

Holds the road network (nodes, segments), the networkx graph built from it,
and calculated routes (kept so a route can be retrieved again by id after
calculation — see api/routes_routing.py). Vehicles, weather, incidents, and
the activity log described in ARCHITECTURE.md are later scope and
intentionally not modeled here yet — adding empty placeholders for them now
would just be dead state with nothing to read or write it.
"""
from app.core.routing_engine import build_graph
from app.data.network_loader import load_network
from app.models.route import Route


class StateStore:
    def __init__(self):
        self.nodes = []
        self.segments = []
        self.graph = None
        self._routes: dict[str, Route] = {}

    def load(self):
        self.nodes, self.segments = load_network()
        self.graph = build_graph(self.nodes, self.segments)
        self._routes = {}

    def get_nodes(self):
        return self.nodes

    def get_segments(self):
        return self.segments

    def get_segment(self, segment_id: str):
        for segment in self.segments:
            if segment.id == segment_id:
                return segment
        return None

    def add_route(self, route: Route) -> None:
        self._routes[route.route_id] = route

    def get_route(self, route_id: str) -> Route | None:
        return self._routes.get(route_id)


state_store = StateStore()
