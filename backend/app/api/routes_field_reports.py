"""
POST /field-reports               submit a field worker's GPS-tagged incident
                                   report; maps it to the nearest real OSM
                                   segment and feeds it into the EXISTING
                                   Part 8 hazard/risk/reroute pipeline
GET  /field-reports                list field reports (active only by default)
GET  /field-reports/{id}           one field report
POST /field-reports/{id}/resolve   mark a report resolved; clears ITS hazard
                                    only (independent active reports on the
                                    same segment are left untouched)

Part 12. Thin by design, matching every other routes_*.py module: all
GPS-matching/hazard-construction logic lives in
core/field_report_service.py, all risk/routing/reroute logic in the
UNCHANGED core/risk_engine.py, core/routing_engine.py, core/reroute_service.py
-- this module only validates input, calls those, and shapes the response.

Every FieldReport is explicitly source="field_report" -- a real (prototype)
user-submitted observation, never presented as a GSI/APSAC/IMD record and
never labeled "SIMULATED" (contrast with Part 8's demo hazards, see
app/models/hazard.py). Offline queue/synchronization is reserved for future
production implementation -- see FieldReport.methodology_note.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.field_report_service import NoNearbyRoadError, create_field_report
from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.risk_engine import assess_segment_risk
from app.core.routing_engine import Location, NoRouteFoundError, UnknownLocationError
from app.models.field_report import FieldIncidentType, FieldReport
from app.models.hazard import HazardEvent, HazardSeverity
from app.models.network import GeoPoint
from app.models.risk import RiskResult
from app.models.route import RouteDecision
from app.store.state_store import state_store

router = APIRouter(prefix="/field-reports")


class FieldReportCreateRequest(BaseModel):
    incident_type: FieldIncidentType
    severity: HazardSeverity
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    description: str = Field(min_length=1)
    reporter_name: Optional[str] = None

    # Optional: if a currently-displayed/dispatched route is given, evaluate
    # its CONTINUE/REROUTE/SUSPEND impact in the SAME response (Part 12 step
    # 8), reusing core/reroute_service.py::evaluate_route_decision()
    # UNCHANGED -- exactly what HazardControl.jsx already does for Part 8's
    # simulated hazards via POST /routes/evaluate-disruption, just folded
    # into one call. Omit all three to just log the report with no route
    # evaluation.
    origin: Optional[str | GeoPoint] = None
    destination: Optional[str | GeoPoint] = None
    previous_route_id: Optional[str] = None


class FieldReportResolveRequest(BaseModel):
    # Same optional route-impact fields as create -- lets the UI show the
    # route decision immediately after resolving, mirroring
    # HazardControl.jsx's clear-hazard flow.
    origin: Optional[str | GeoPoint] = None
    destination: Optional[str | GeoPoint] = None
    previous_route_id: Optional[str] = None


class FieldReportResponse(BaseModel):
    report: FieldReport
    hazard_event: HazardEvent
    current_risk: RiskResult
    route_decision: Optional[RouteDecision] = None


def _current_risk_for(segment_id: str) -> RiskResult:
    """Same computation GET /segments/{id}/risk-aware performs (see
    api/routes_network.py) -- not a new calculation, just reused inline so a
    field-report response already shows the post-hazard risk without a
    second round trip."""
    segment = state_store.get_segment(segment_id)
    active_hazards = state_store.get_hazards(active_only=True)
    context = combine_active_hazards_into_segment_context(active_hazards).get(segment_id)
    weather_factor = context.weather_factor if context else None
    incident_factor = context.incident_factor if context else None
    return assess_segment_risk(segment, weather_factor=weather_factor, incident_factor=incident_factor)


def _route_decision_for(
    origin: Optional[Location], destination: Optional[Location], previous_route_id: Optional[str]
) -> Optional[RouteDecision]:
    """Reuses core/reroute_service.py::evaluate_route_decision() UNCHANGED
    against every currently active hazard (this field report's own hazard
    included, since it was already added to StateStore before this is
    called) -- the exact same function every other CONTINUE/REROUTE/SUSPEND
    surface in this app uses. None when no origin/destination was supplied
    (nothing to evaluate)."""
    if origin is None or destination is None:
        return None
    previous_route = state_store.get_route(previous_route_id) if previous_route_id else None
    active_hazards = state_store.get_hazards(active_only=True)
    segment_context = combine_active_hazards_into_segment_context(active_hazards)

    decision = evaluate_route_decision(
        state_store.graph,
        state_store.get_nodes(),
        state_store.get_segments(),
        origin,
        destination,
        previous_route=previous_route,
        segment_context=segment_context,
        active_hazard_ids=[h.id for h in active_hazards],
    )
    if decision.recommended_route is not None:
        state_store.add_route(decision.recommended_route)
    return decision


@router.post("", response_model=FieldReportResponse)
def create_field_report_endpoint(request: FieldReportCreateRequest):
    try:
        report, hazard_event = create_field_report(
            request.incident_type,
            request.severity,
            request.latitude,
            request.longitude,
            request.description,
            state_store.get_segments(),
            state_store.get_field_reports(),
            reporter_name=request.reporter_name,
        )
    except NoNearbyRoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state_store.add_hazard(hazard_event)
    state_store.add_field_report(report)

    try:
        route_decision = _route_decision_for(request.origin, request.destination, request.previous_route_id)
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FieldReportResponse(
        report=report,
        hazard_event=hazard_event,
        current_risk=_current_risk_for(report.segment_id),
        route_decision=route_decision,
    )


@router.get("", response_model=list[FieldReport])
def list_field_reports(active_only: bool = True):
    return state_store.get_field_reports(active_only=active_only)


@router.get("/{report_id}", response_model=FieldReport)
def get_field_report(report_id: str):
    report = state_store.get_field_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Unknown field report: {report_id}")
    return report


@router.post("/{report_id}/resolve", response_model=FieldReportResponse)
def resolve_field_report_endpoint(report_id: str, request: Optional[FieldReportResolveRequest] = None):
    request = request or FieldReportResolveRequest()
    report = state_store.get_field_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Unknown field report: {report_id}")

    # Clears ONLY this report's own hazard (StateStore.clear_hazard is
    # per-id) -- a different field report's hazard on the same segment is
    # untouched, even if it shares a segment_id (Part 12 section 6).
    if report.hazard_event_id is not None:
        state_store.clear_hazard(report.hazard_event_id)
    resolved = state_store.resolve_field_report(report_id)
    hazard_event = state_store.get_hazard(resolved.hazard_event_id)

    try:
        route_decision = _route_decision_for(request.origin, request.destination, request.previous_route_id)
    except UnknownLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FieldReportResponse(
        report=resolved,
        hazard_event=hazard_event,
        current_risk=_current_risk_for(resolved.segment_id),
        route_decision=route_decision,
    )
