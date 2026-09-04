from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.network import GeoPoint


def _new_route_id() -> str:
    return f"route_{uuid4().hex[:12]}"


class Route(BaseModel):
    """
    The result of a route calculation. Deliberately algorithm-agnostic: it
    just describes a path (nodes, segments, geometry, totals) with no
    reference to Dijkstra/A*/cost function, so routing_engine's internals can
    change (e.g. risk-aware cost later) without this model changing.

    Also deliberately vehicle-agnostic for now (no vehicle_id) — a future
    vehicle/rerouting system can reference a Route by route_id rather than
    this model depending on that system.
    """

    route_id: str = Field(default_factory=_new_route_id)
    origin: str  # resolved origin node_id
    destination: str  # resolved destination node_id
    node_ids: list[str]
    segment_ids: list[str]
    total_distance_km: float
    estimated_travel_time_min: float
    geometry: list[GeoPoint]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
