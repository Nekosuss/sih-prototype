"""
HazardEvent — a deterministic, prototype SIMULATED hazard/context override
used to demonstrate dynamic risk reassessment and route decisions (Part 8).

This is NOT the fuller Incident/WeatherCondition field-reporting design
sketched in ARCHITECTURE.md section 6 and stubbed in app/models/incident.py
/ app/models/weather.py (a persistent, geo-tagged, field-officer-reported
Incident; a live WeatherCondition with condition_type/intensity/updated_at)
— that full workflow (field-reporting UI, a live weather feed) is later
scope. HazardEvent is a deliberately simpler, unified concept covering both
for prototype demonstration: one event type that maps directly onto the
Part 5 risk engine's existing weather_factor/incident_factor inputs
(app/core/risk_engine.py::assess_segment_risk), using the SAME severity
vocabulary the risk engine already defined for incidents
(INCIDENT_SEVERITY_FACTOR / incident_factor_from_severity, app/config.py)
— minor/major/blocking — rather than inventing a second one.

Every HazardEvent is explicitly a SIMULATION for demo purposes, never a
live weather/field observation — see HAZARD_TYPE_LABEL below, which is
used everywhere a hazard is described in an API response or the UI.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_hazard_id() -> str:
    return f"hazard_{uuid4().hex[:12]}"


class HazardType(str, Enum):
    heavy_rain = "heavy_rain"
    landslide = "landslide"
    road_blockage = "road_blockage"


class HazardSeverity(str, Enum):
    minor = "minor"
    major = "major"
    blocking = "blocking"


HAZARD_TYPE_LABEL = {
    HazardType.heavy_rain: "SIMULATED HEAVY RAIN",
    HazardType.landslide: "SIMULATED LANDSLIDE",
    HazardType.road_blockage: "SIMULATED ROAD BLOCKAGE",
}


class HazardEvent(BaseModel):
    """
    affected_segment_ids: real RoadSegment ids only (validated by the API
    layer, app/api/routes_hazards.py, before this is ever constructed).

    weather_factor/incident_factor: derived from (type, severity) at
    creation time (see app/core/hazard_state.py::build_hazard_event) via
    the exact same config-driven mapping the rest of the app uses — never
    a value invented ad hoc per event. Exactly one of the two is set,
    matching which risk-engine component this hazard type feeds (heavy
    rain -> weather_risk; landslide/road_blockage -> incident_risk).

    active/cleared_at: a hazard is never deleted on clear (see
    StateStore.clear_hazard) — it's marked inactive so the demo can show
    hazard HISTORY, not just current state, and so clearing is reversible
    to inspect (though not to re-activate; use /hazards/simulate again for
    that).
    """

    id: str = Field(default_factory=_new_hazard_id)
    type: HazardType
    severity: HazardSeverity
    affected_segment_ids: list[str]
    weather_factor: Optional[float] = None
    incident_factor: Optional[float] = None
    message: str
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cleared_at: Optional[datetime] = None
