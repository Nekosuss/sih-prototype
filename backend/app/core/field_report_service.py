"""
Turns a field worker's GPS-tagged incident report into a real HazardEvent
feeding the EXISTING Part 8 hazard/risk/reroute pipeline
(core/hazard_state.py, core/risk_engine.py, core/routing_engine.py,
core/reroute_service.py) -- this module computes NO new risk or routing
logic of its own. It only:

  1. Maps a raw lat/lng to the nearest REAL OSM road segment
     (core/geo.py::nearest_point_on_polyline) -- real geometry only, never a
     fabricated road, nearest-town snap, or straight-line corridor guess --
     and rejects reports too far from any real road
     (app/config.py::FIELD_REPORT_MAX_SNAP_DISTANCE_M).
  2. Builds a HazardEvent (app/models/hazard.py) using the exact same
     severity->factor derivation Part 8's simulated hazards already use
     (core/hazard_state.py's weather_factor_for_severity/
     incident_factor_for_severity) -- see below for why it doesn't call
     build_hazard_event() itself.
  3. Flags (never discards) a simple deterministic possible-duplicate
     signal (Part 12 section 13).

api/routes_field_reports.py owns all StateStore reads/writes (add_hazard,
add_field_report, clear_hazard, ...), exactly mirroring how
api/routes_hazards.py is the only place core/hazard_state.py's output
actually gets stored -- this module stays pure/testable, like
hazard_state.py itself.

--- Field-report incident type -> underlying HazardType ---

FieldIncidentType (app/models/field_report.py) is a real-world reporting
vocabulary, deliberately broader than HazardType's small demo-simulation one
(heavy_rain/landslide/road_blockage). See
app/config.py::FIELD_REPORT_INCIDENT_TO_HAZARD_TYPE for the mapping and full
reasoning: every field incident type except landslide maps onto
HazardType.road_blockage, so a "blocking" field report of any of them closes
its segment exactly like a simulated blocking road_blockage hazard does
(HAZARD_CLOSURE_TYPES/HAZARD_CLOSURE_SEVERITY, app/config.py). No field
report ever produces a HazardType.heavy_rain hazard (weather_factor stays
unused here) -- a field-reported incident is, by definition, an observed
event at a place, not a weather condition.

--- Why this doesn't call core/hazard_state.py::build_hazard_event() ---

build_hazard_event() hardcodes HAZARD_TYPE_LABEL's "SIMULATED ..." message --
correct for Part 8's demo controls, actively misleading for a field report
(Part 12 Data Integrity rule 2: label the source as field_report, never as a
verified record or a demo simulation). This module therefore constructs the
HazardEvent directly, reusing ONLY the pure factor-derivation functions --
never inventing a second risk/hazard engine, never duplicating
INCIDENT_SEVERITY_FACTOR's numbers.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import (
    FIELD_REPORT_DUPLICATE_WINDOW_MINUTES,
    FIELD_REPORT_INCIDENT_TO_HAZARD_TYPE,
    FIELD_REPORT_MAX_SNAP_DISTANCE_M,
)
from app.core.geo import nearest_point_on_polyline
from app.core.hazard_state import incident_factor_for_severity
from app.models.field_report import FieldIncidentType, FieldReport, FieldReportStatus
from app.models.hazard import HazardEvent, HazardSeverity, HazardType
from app.models.network import RoadSegment


class NoNearbyRoadError(ValueError):
    """Raised when a reported location is farther than
    max_snap_distance_m from every real road segment currently loaded --
    the API layer maps this to a 400. Never silently snapped onto a distant
    road."""


def hazard_type_for_incident(incident_type: FieldIncidentType) -> HazardType:
    """See app/config.py::FIELD_REPORT_INCIDENT_TO_HAZARD_TYPE for the
    mapping and full reasoning."""
    return HazardType(FIELD_REPORT_INCIDENT_TO_HAZARD_TYPE[incident_type.value])


def find_nearest_segment(lat: float, lng: float, segments: list[RoadSegment]) -> tuple[Optional[RoadSegment], Optional[float]]:
    """Returns (nearest_segment, distance_m) -- the true nearest point on
    any currently-loaded segment's REAL geometry (core/geo.py::
    nearest_point_on_polyline checks every real polyline edge, never just
    endpoints). Returns (None, None) if `segments` is empty."""
    best_segment: Optional[RoadSegment] = None
    best_distance_km: Optional[float] = None
    for segment in segments:
        points = [(p.lat, p.lng) for p in segment.geometry]
        _, _, distance_km = nearest_point_on_polyline(lat, lng, points)
        if best_distance_km is None or distance_km < best_distance_km:
            best_segment, best_distance_km = segment, distance_km
    if best_segment is None:
        return None, None
    return best_segment, best_distance_km * 1000.0


def build_field_hazard_event(incident_type: FieldIncidentType, severity: HazardSeverity, segment_id: str) -> HazardEvent:
    """Mirrors core/hazard_state.py::build_hazard_event's factor derivation
    exactly (same INCIDENT_SEVERITY_FACTOR table, app/config.py) -- see
    module docstring for why it's not called directly."""
    hazard_type = hazard_type_for_incident(incident_type)
    incident_factor = incident_factor_for_severity(severity)
    message = (
        f"FIELD REPORT: {incident_type.value.replace('_', ' ')} ({severity.value} severity) "
        f"reported by a field worker, affecting segment {segment_id}. User-submitted field "
        f"observation -- not a verified GSI/APSAC/IMD record, and not a Part 8 SIMULATED "
        f"demo input."
    )
    return HazardEvent(
        type=hazard_type,
        severity=severity,
        affected_segment_ids=[segment_id],
        incident_factor=incident_factor,
        message=message,
    )


def is_possible_duplicate(
    existing_reports: list[FieldReport],
    incident_type: FieldIncidentType,
    segment_id: str,
    now: Optional[datetime] = None,
) -> bool:
    """Simple deterministic duplicate signal (Part 12 section 13) -- an
    ACTIVE report of the SAME incident type on the SAME matched segment,
    reported within FIELD_REPORT_DUPLICATE_WINDOW_MINUTES of now. Never
    discards or merges anything; both reports are always kept as
    independent records with independent hazards."""
    now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=FIELD_REPORT_DUPLICATE_WINDOW_MINUTES)
    for report in existing_reports:
        if (
            report.status == FieldReportStatus.active
            and report.incident_type == incident_type
            and report.segment_id == segment_id
            and (now - report.created_at) <= window
        ):
            return True
    return False


def create_field_report(
    incident_type: FieldIncidentType,
    severity: HazardSeverity,
    latitude: float,
    longitude: float,
    description: str,
    segments: list[RoadSegment],
    existing_reports: list[FieldReport],
    reporter_name: Optional[str] = None,
    max_snap_distance_m: float = FIELD_REPORT_MAX_SNAP_DISTANCE_M,
) -> tuple[FieldReport, HazardEvent]:
    """
    Pure construction -- does not touch StateStore (see
    api/routes_field_reports.py for that), exactly mirroring
    core/hazard_state.py::build_hazard_event()'s division of responsibility.
    Raises NoNearbyRoadError if the reported location is too far from every
    real road segment in `segments`.
    """
    if not segments:
        raise NoNearbyRoadError("No road segments are loaded to match this report against.")

    segment, distance_m = find_nearest_segment(latitude, longitude, segments)
    if distance_m > max_snap_distance_m:
        raise NoNearbyRoadError(
            f"Reported location ({latitude}, {longitude}) is {distance_m:.0f}m from the "
            f"nearest real road segment ({segment.id}), beyond the maximum snap distance of "
            f"{max_snap_distance_m:.0f}m. Refusing to snap this report onto a distant road."
        )

    hazard_event = build_field_hazard_event(incident_type, severity, segment.id)
    duplicate = is_possible_duplicate(existing_reports, incident_type, segment.id)

    report = FieldReport(
        reporter_name=reporter_name,
        incident_type=incident_type,
        severity=severity,
        latitude=latitude,
        longitude=longitude,
        description=description,
        segment_id=segment.id,
        segment_name=segment.name,
        distance_to_road_m=round(distance_m, 1),
        hazard_event_id=hazard_event.id,
        possible_duplicate=duplicate,
    )
    return report, hazard_event
