"""
FieldReport — a real (prototype) field-worker-submitted incident report,
geo-matched to an actual OSM road segment and fed into the EXISTING Part 8
hazard/risk/reroute pipeline (see app/core/field_report_service.py).

This is deliberately NOT a second hazard/risk concept: FieldReport is its
own record (what a field worker reported, where, and its lifecycle), but the
thing it actually feeds into routing/risk is a plain app/models/hazard.py
HazardEvent -- the exact same model Part 8's simulated demo hazards use. A
FieldReport's `hazard_event_id` is that link.

--- Why this is a separate model from HazardEvent, not a subclass/variant ---

HazardEvent is deliberately source-agnostic (Part 8's docstring: "one event
type... for prototype demonstration"). FieldReport carries everything that
is specific to a REAL field observation and meaningless for a simulated demo
hazard -- who reported it, the raw GPS coordinates, how far that was from
the matched road, the report's own lifecycle (active/resolved) -- without
adding any of that noise to HazardEvent or changing what Part 8 already
relies on.

--- severity reuses app.models.hazard.HazardSeverity ---

minor/major/blocking -- the exact same vocabulary INCIDENT_SEVERITY_FACTOR
(app/config.py) and HAZARD_CLOSURE_SEVERITY already define. A field report
never invents its own severity scale.

--- source ---

Always the literal string "field_report" -- never presented as a GSI/APSAC/
IMD record, and never labeled "SIMULATED" (contrast with HAZARD_TYPE_LABEL
in app/models/hazard.py, which every Part 8 demo hazard message is prefixed
with). See core/field_report_service.py for why a field report's HazardEvent
message is built separately from build_hazard_event()'s SIMULATED-labeled one.

--- Offline sync ---

Not implemented (Part 12 explicitly excludes it) -- but `id`/`created_at`/
`status`/`source` and this model always being the entire, explicit server
response to a create/resolve call are exactly the shape a future offline
queue would need to reconcile against. Documented here, not built.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.hazard import HazardSeverity


def _new_field_report_id() -> str:
    return f"report_{uuid4().hex[:12]}"


class FieldIncidentType(str, Enum):
    """A real-world field-reporting vocabulary -- deliberately broader than
    app/models/hazard.py's HazardType (heavy_rain/landslide/road_blockage),
    which exists only for Part 8's small demo-simulation control panel. See
    core/field_report_service.py for how each of these maps onto the
    existing HazardType/severity->factor machinery without inventing a
    second one."""

    landslide = "landslide"
    road_blockage = "road_blockage"
    flooding = "flooding"
    accident = "accident"
    fallen_tree = "fallen_tree"
    damaged_road = "damaged_road"
    other = "other"


class FieldReportStatus(str, Enum):
    active = "active"
    resolved = "resolved"


class FieldReport(BaseModel):
    id: str = Field(default_factory=_new_field_report_id)
    reporter_name: Optional[str] = None
    incident_type: FieldIncidentType
    severity: HazardSeverity
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    description: str = Field(min_length=1)

    # Real GPS -> real OSM segment matching result (core/geo.py::
    # nearest_point_on_polyline via core/field_report_service.py) -- never a
    # fabricated/nearest-town/straight-line guess. segment_name is a
    # read-only convenience copy of RoadSegment.name at match time.
    segment_id: str
    segment_name: Optional[str] = None
    distance_to_road_m: float = Field(ge=0.0)

    status: FieldReportStatus = FieldReportStatus.active
    # The HazardEvent (app/models/hazard.py) this report created -- the
    # SAME model/pipeline every other hazard source uses. None only if
    # report construction somehow ran without creating one (never happens
    # via core/field_report_service.py::create_field_report).
    hazard_event_id: Optional[str] = None

    # Part 12 section 13: a simple deterministic signal only -- an ACTIVE
    # report of the same incident_type on the same matched segment within a
    # short window (see app/config.py::FIELD_REPORT_DUPLICATE_WINDOW_MINUTES).
    # Never causes a report to be discarded or merged; both are always kept
    # as independent records with independent hazards.
    possible_duplicate: bool = False

    source: str = "field_report"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

    methodology_note: str = (
        "User-submitted field observation -- not a verified GSI/APSAC/IMD record, and not "
        "a Part 8 SIMULATED demo input. Offline queue/synchronization is reserved for a "
        "future production implementation; this prototype requires network connectivity "
        "at submission time."
    )
