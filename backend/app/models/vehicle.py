"""
Vehicle — a DETERMINISTIC SIMULATED logistics vehicle moving along a real
route (Part 9). This is NOT real GPS and must never be presented as live
vehicle tracking: current_lat/current_lng are computed by interpolating
along the ACTUAL geometry of a real route returned by core/routing_engine.py
(core/geo.py::interpolate_along_path), advanced purely as a function of
real elapsed wall-clock time and a configurable simulated speed
(app/config.py::SIMULATION_SPEED_KMPH) — no randomness, no external
GPS/location hardware, no physics model.

See app/simulation/vehicle_simulator.py for how a Vehicle's position is
computed/advanced and how it reacts to Part 8's hazard/reroute system —
this module is the data shape only.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.route import Route, RouteRiskProfile


def _new_vehicle_id() -> str:
    return f"vehicle_{uuid4().hex[:12]}"


class VehicleStatus(str, Enum):
    idle = "idle"  # created, route computed, movement not started yet
    en_route = "en_route"  # actively moving along current_route
    rerouting = "rerouting"  # a hazard just forced a route change (one-tick transitional state)
    arrived = "arrived"  # reached the destination
    suspended = "suspended"  # no safe route currently exists (Part 8 SUSPEND) -- frozen in place


class Vehicle(BaseModel):
    id: str = Field(default_factory=_new_vehicle_id)
    name: str

    # The vehicle's TRUE journey endpoints — kept fixed across reroutes so
    # the original dispatch intent is always visible, even though
    # current_route may change (see vehicle_simulator.py's simplified
    # reroute handling: current_route is replaced by a fresh real route
    # from the vehicle's current node to `destination`, not spliced).
    origin: str
    destination: str

    current_route: Optional[Route] = None
    current_segment_id: Optional[str] = None
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None

    # Progress ALONG current_route specifically (0.0 at the start of the
    # route currently assigned, 1.0 at its end) — not of the original
    # end-to-end journey, since a reroute can change the remaining
    # distance. distance_travelled_km / distance_remaining_km are in the
    # same "current route" frame for the same reason.
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    distance_travelled_km: float = 0.0
    distance_remaining_km: float = 0.0
    eta_minutes: Optional[float] = None  # remaining time at speed_kmph, real elapsed-time based

    speed_kmph: float = 60.0  # deterministic SIMULATED speed — see app/config.py::SIMULATION_SPEED_KMPH
    status: VehicleStatus = VehicleStatus.idle
    paused: bool = False  # orthogonal to status: freezes position advancement without changing status

    route_risk: Optional[RouteRiskProfile] = None
    last_decision_reason: Optional[str] = None  # human-readable reason for the most recent CONTINUE/REROUTE/SUSPEND check

    # --- internal timing anchors (Part 9) ---
    # Position is always recomputed FRESH from these on every read/tick —
    # never accumulated step by step — specifically to avoid the
    # cumulative floating-point drift a repeated "add a small delta every
    # tick" approach would risk over a long-running demo.
    route_started_at: Optional[datetime] = None  # wall-clock anchor for current_route's timing
    paused_since: Optional[datetime] = None  # set while paused/suspended; None otherwise
    paused_seconds_total: float = 0.0  # accumulated pause/suspend duration for current_route

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    methodology_note: str = (
        "Deterministic SIMULATED vehicle position along a real route — not live GPS "
        "or real-time tracking."
    )
