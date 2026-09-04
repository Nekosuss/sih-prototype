"""
Single in-memory source of truth.

Holds the road network (nodes, segments), the networkx graph built from it,
calculated routes (kept so a route can be retrieved again by id after
calculation — see api/routes_routing.py), (Part 8) active/cleared simulated
HazardEvents, and (Part 9) simulated Vehicles. The activity log described in
ARCHITECTURE.md is later scope and intentionally not modeled here yet —
adding an empty placeholder for it now would just be dead state with
nothing to read or write it.

Hazard/vehicle storage is deliberately as thin as route storage: a dict
keyed by id, no query logic beyond what callers actually need. Turning a
hazard into per-segment weather/incident context
(hazard_state.SegmentHazardContext) and advancing a vehicle's simulated
position (simulation/vehicle_simulator.py) are NOT this class's job —
StateStore only stores and retrieves, exactly like it already does for
routes.
"""
from datetime import datetime, timezone

from app.core.routing_engine import build_graph
from app.data.network_loader import load_network
from app.models.field_report import FieldReport, FieldReportStatus
from app.models.hazard import HazardEvent
from app.models.route import Route
from app.models.vehicle import Vehicle


class StateStore:
    def __init__(self):
        self.nodes = []
        self.segments = []
        self.graph = None
        self._routes: dict[str, Route] = {}
        self._hazards: dict[str, HazardEvent] = {}
        self._vehicles: dict[str, Vehicle] = {}
        self._field_reports: dict[str, FieldReport] = {}

    def load(self):
        self.nodes, self.segments = load_network()
        self.graph = build_graph(self.nodes, self.segments)
        self._routes = {}
        self._hazards = {}
        self._vehicles = {}
        self._field_reports = {}

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

    # --- Part 8: simulated hazard events ---

    def add_hazard(self, event: HazardEvent) -> None:
        self._hazards[event.id] = event

    def get_hazard(self, hazard_id: str) -> HazardEvent | None:
        return self._hazards.get(hazard_id)

    def get_hazards(self, active_only: bool = False) -> list[HazardEvent]:
        events = list(self._hazards.values())
        if active_only:
            events = [e for e in events if e.active]
        return sorted(events, key=lambda e: e.created_at)

    def clear_hazard(self, hazard_id: str) -> HazardEvent | None:
        """Marks a hazard inactive (never deletes it — see HazardEvent's
        docstring for why). Returns the updated event, or None if
        hazard_id is unknown. Clearing an already-inactive hazard is a
        no-op that returns it unchanged (idempotent)."""
        event = self._hazards.get(hazard_id)
        if event is None or not event.active:
            return event
        cleared = event.model_copy(update={"active": False, "cleared_at": datetime.now(timezone.utc)})
        self._hazards[hazard_id] = cleared
        return cleared

    def reset_hazards(self) -> None:
        """Deterministic demo reset (Part 8 section 10) — removes ALL
        hazard history, active or cleared, distinct from clear_hazard()
        which keeps a record. Does not touch nodes/segments/graph/routes."""
        self._hazards = {}

    # --- Part 9: simulated vehicles ---

    def add_vehicle(self, vehicle: Vehicle) -> None:
        self._vehicles[vehicle.id] = vehicle

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        return self._vehicles.get(vehicle_id)

    def get_vehicles(self) -> list[Vehicle]:
        return sorted(self._vehicles.values(), key=lambda v: v.created_at)

    def remove_vehicle(self, vehicle_id: str) -> bool:
        return self._vehicles.pop(vehicle_id, None) is not None

    # --- Part 12: field reports ---

    def add_field_report(self, report: FieldReport) -> None:
        self._field_reports[report.id] = report

    def get_field_report(self, report_id: str) -> FieldReport | None:
        return self._field_reports.get(report_id)

    def get_field_reports(self, active_only: bool = False) -> list[FieldReport]:
        reports = list(self._field_reports.values())
        if active_only:
            reports = [r for r in reports if r.status == FieldReportStatus.active]
        return sorted(reports, key=lambda r: r.created_at)

    def resolve_field_report(self, report_id: str) -> FieldReport | None:
        """Marks a field report resolved (idempotent, like clear_hazard()).
        Does NOT itself clear the linked hazard -- the caller (see
        api/routes_field_reports.py) clears that hazard by id explicitly,
        so resolving report A never touches a hazard created by a
        different report B, even if both affect the same segment."""
        report = self._field_reports.get(report_id)
        if report is None or report.status == FieldReportStatus.resolved:
            return report
        resolved = report.model_copy(update={"status": FieldReportStatus.resolved, "resolved_at": datetime.now(timezone.utc)})
        self._field_reports[report_id] = resolved
        return resolved


state_store = StateStore()
