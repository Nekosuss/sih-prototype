from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.network import GeoPoint
from app.models.risk import RiskResult


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


# ---------------------------------------------------------------------------
# Part 6: risk-aware routing result shapes.
# See core/routing_engine.py's "risk-aware routing" section for how these
# are computed — this is the output shape only.
# ---------------------------------------------------------------------------


class RouteSafetyOutcome(str, Enum):
    """Which of the three route-comparison cases (Part 6 section 5) applied.
    Deliberately NOT named PROCEED/REROUTE/SUSPEND — that decision
    state machine is later scope (a future part). These three values are
    chosen so that part can build on top of them without this module
    needing to change: fastest_route_is_safe / safer_route_selected map
    fairly directly to "proceed", no_safe_route_available to "suspend /
    escalate", but that mapping is deliberately left to that later part."""

    fastest_route_is_safe = "fastest_route_is_safe"
    safer_route_selected = "safer_route_selected"
    no_safe_route_available = "no_safe_route_available"


class RouteRiskProfile(BaseModel):
    """
    Explainable PROTOTYPE risk profile for one already-computed Route —
    NOT a calibrated probability of a landslide or disruption occurring on
    this route. See core/routing_engine.py::compute_route_risk_profile for
    the aggregation method (deliberately not a plain average of segment
    risk scores — see that function's docstring for why).
    """

    aggregate_risk_score: float = Field(ge=0.0, le=1.0)
    max_segment_risk: float = Field(ge=0.0, le=1.0)
    max_risk_segment_id: Optional[str] = None
    segment_count_by_risk_level: dict[str, int]
    unsafe_segment_count: int
    hard_unsafe_threshold: float
    weather_factor_used: Optional[float] = None
    incident_factor_used: Optional[float] = None
    methodology_note: str = (
        "Explainable prototype route-level risk aggregation (a weighted blend of "
        "mean and maximum segment risk, weighted towards the maximum so one "
        "dangerous segment is never diluted away). NOT a calibrated probability."
    )


class RiskAwareRouteResult(BaseModel):
    """
    The result of comparing the fastest (travel-time-only) route against the
    risk-aware route for the same origin/destination (Part 6 section 5).
    `recommended_route`/`recommended_route_risk` are None only when `outcome`
    is `no_safe_route_available` — every other case always has a
    recommendation (which may simply be the fastest route itself).
    """

    outcome: RouteSafetyOutcome
    fastest_route: Route
    fastest_route_risk: RouteRiskProfile
    recommended_route: Optional[Route] = None
    recommended_route_risk: Optional[RouteRiskProfile] = None
    safer_alternative_selected: bool
    unsafe_segments_in_fastest_route: bool
    reasons: list[str]

    # Per-segment RiskResult for every segment on each route, in the same
    # order as the route's own segment_ids (Part 6.5 UI integration). This
    # is NOT a new calculation — compute_route_risk_profile() already
    # computes exactly these per-segment results internally to build its
    # aggregate; this just also exposes the list it was previously
    # discarding, so a caller (e.g. the map UI) can show/inspect individual
    # route segments without N separate requests or re-deriving risk in
    # JavaScript.
    #
    # recommended_route_segment_risks is None in TWO cases: no safe route
    # exists (recommended_route is also None), OR the recommended route IS
    # the fastest route (outcome == fastest_route_is_safe) — in that second
    # case the two routes' segments (and therefore risks) are identical, so
    # this is left None rather than sending the same ~250KB list twice; a
    # caller should fall back to fastest_route_segment_risks whenever
    # recommended_route_segment_risks is None but recommended_route is not.
    fastest_route_segment_risks: list[RiskResult] = Field(default_factory=list)
    recommended_route_segment_risks: Optional[list[RiskResult]] = None


# ---------------------------------------------------------------------------
# Part 8: dynamic hazard response — the CONTINUE/REROUTE/SUSPEND decision.
# See core/reroute_service.py for how these are computed.
# ---------------------------------------------------------------------------


class RouteDecisionOutcome(str, Enum):
    """`continue` is a Python keyword, hence the member name `continue_` —
    the wire value (JSON/API) is still the plain word "continue"."""

    continue_ = "continue"
    reroute = "reroute"
    suspend = "suspend"


class RouteDecision(BaseModel):
    """
    Whether an origin/destination's route should CONTINUE, REROUTE, or be
    SUSPENDed, given the current dynamic hazard state (Part 8) — see
    core/reroute_service.py::evaluate_route_decision. Reuses
    compare_fastest_and_safe_routes() (Part 6) internally; this model adds
    no new risk/routing computation of its own, only the decision +
    hysteresis layer on top (see reroute_service.py for the hysteresis
    margin and why it exists).

    previous_route: the route this decision is evaluated AGAINST (e.g. a
    route calculated earlier in the demo) — optional. When omitted, there
    is nothing to be "sticky" about, so the outcome is either `continue`
    (a safe recommendation exists) or `suspend` (it doesn't); `reroute`
    only ever appears when a previous_route was actually provided and is
    no longer the recommended choice.

    recommended_route is None only when outcome is `suspend` — no
    fabricated replacement route is ever returned in that case.
    """

    outcome: RouteDecisionOutcome
    origin: str
    destination: str
    previous_route: Optional[Route] = None
    recommended_route: Optional[Route] = None
    previous_route_risk: Optional[RouteRiskProfile] = None
    recommended_route_risk: Optional[RouteRiskProfile] = None
    # Every segment_id currently under an active hazard (network-wide, not
    # just on these two routes) — lets a caller show "here's what's
    # currently disrupted" even if it turns out not to affect this
    # particular origin/destination.
    affected_segment_ids: list[str] = Field(default_factory=list)
    active_hazard_ids: list[str] = Field(default_factory=list)
    eta_change_min: Optional[float] = None
    reason: str
    methodology_note: str = (
        "Explainable prototype route decision based on real graph routes and the "
        "Part 5 explainable prototype risk engine plus deterministic simulated "
        "hazard context. NOT a calibrated probability, and not based on live "
        "weather or field data."
    )
