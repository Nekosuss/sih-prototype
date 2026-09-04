"""
Combines active HazardEvent objects (Part 8 — app/models/hazard.py) into
per-segment dynamic context that routing_engine.py's risk-aware routing can
apply on top of the SAME risk_engine.assess_segment_risk() formula (Part 5)
— this module computes NO risk itself, it only prepares the
weather_factor/incident_factor/closed overrides that assess_segment_risk()
already accepts as optional current-context inputs.

--- STATIC vs DYNAMIC (see also backend/app/data/README.md) ---

RoadSegment.elevation_m / slope_deg / historical_landslide_count /
nearest_landslide_distance_m are the real DEM/GSI-derived STATIC base
features (Part 4.8 / the GSI spatial join) and are never read, written, or
overridden anywhere in this module. A hazard only ever produces a DYNAMIC
weather_factor/incident_factor for assess_segment_risk() to combine with
those static features at scoring time — it cannot and does not mutate the
RoadSegment objects themselves. Clearing a hazard (StateStore.clear_hazard)
simply removes its contribution from this combination on the next call;
there is nothing to "restore" on the segment because nothing on it was ever
changed.

--- Every hazard here is a SIMULATION ---

HAZARD_TYPE_LABEL (app/models/hazard.py) prefixes every human-readable
hazard description with "SIMULATED" — there is no live weather feed or
field-report ingestion in this system. See build_hazard_event()'s message.
"""
from dataclasses import dataclass
from typing import Optional

from app.config import (
    HAZARD_CLOSURE_SEVERITY,
    HAZARD_CLOSURE_TYPES,
    INCIDENT_SEVERITY_FACTOR,
    WEATHER_SEVERITY_FACTOR,
)
from app.models.hazard import HAZARD_TYPE_LABEL, HazardEvent, HazardSeverity, HazardType


@dataclass(frozen=True)
class SegmentHazardContext:
    """Per-segment dynamic override for one segment, ready to hand to
    assess_segment_risk()/build_risk_aware_graph(). `closed=True` means
    "operationally unavailable" — excluded from risk-aware routing
    regardless of the computed risk_score (see HAZARD_CLOSURE_TYPES)."""

    weather_factor: Optional[float] = None
    incident_factor: Optional[float] = None
    closed: bool = False


def weather_factor_for_severity(severity: HazardSeverity) -> float:
    return WEATHER_SEVERITY_FACTOR[severity.value]


def incident_factor_for_severity(severity: HazardSeverity) -> float:
    return INCIDENT_SEVERITY_FACTOR[severity.value]


def build_hazard_event(
    hazard_type: HazardType,
    severity: HazardSeverity,
    affected_segment_ids: list[str],
) -> HazardEvent:
    """Pure construction — does not touch StateStore (see
    api/routes_hazards.py for that). Derives weather_factor/incident_factor
    from (type, severity) via the exact same config-driven mapping the rest
    of the app uses (app/config.py) — never an ad hoc per-event number.
    Exactly one of weather_factor/incident_factor is set, matching which
    risk-engine component this hazard type feeds."""
    weather_factor: Optional[float] = None
    incident_factor: Optional[float] = None

    if hazard_type == HazardType.heavy_rain:
        weather_factor = weather_factor_for_severity(severity)
    else:  # landslide, road_blockage
        incident_factor = incident_factor_for_severity(severity)

    label = HAZARD_TYPE_LABEL[hazard_type]
    message = (
        f"{label} ({severity.value} severity) affecting {len(affected_segment_ids)} "
        f"segment(s). Deterministic SIMULATED demo input -- not a live weather "
        f"feed or field observation."
    )

    return HazardEvent(
        type=hazard_type,
        severity=severity,
        affected_segment_ids=list(affected_segment_ids),
        weather_factor=weather_factor,
        incident_factor=incident_factor,
        message=message,
    )


def combine_active_hazards_into_segment_context(hazards: list[HazardEvent]) -> dict[str, SegmentHazardContext]:
    """
    Multiple simultaneous hazards on the same segment combine via MAX per
    factor — the most conservative (least-safe) reading wins. This is
    deliberately not a sum (which could produce hard-to-reason-about values
    once clipped to [0,1] and would let two moderate events look like one
    extreme one) and not an average (which could dilute a single severe
    event with a minor one). A segment is `closed` if ANY active hazard on
    it is a landslide/road_blockage at HAZARD_CLOSURE_SEVERITY ("blocking")
    — see app/config.py::HAZARD_CLOSURE_TYPES for why that specific
    combination bypasses the weighted risk formula instead of just feeding
    into it.

    Only ACTIVE hazards (hazard.active) contribute — a cleared hazard
    (StateStore.clear_hazard) is skipped here even if still present in the
    caller's list, so a stale reference never leaks back in.
    """
    raw: dict[str, dict] = {}
    for hazard in hazards:
        if not hazard.active:
            continue
        is_closure = hazard.type.value in HAZARD_CLOSURE_TYPES and hazard.severity.value == HAZARD_CLOSURE_SEVERITY
        for segment_id in hazard.affected_segment_ids:
            entry = raw.setdefault(segment_id, {"weather_factor": None, "incident_factor": None, "closed": False})
            if hazard.weather_factor is not None:
                entry["weather_factor"] = (
                    hazard.weather_factor
                    if entry["weather_factor"] is None
                    else max(entry["weather_factor"], hazard.weather_factor)
                )
            if hazard.incident_factor is not None:
                entry["incident_factor"] = (
                    hazard.incident_factor
                    if entry["incident_factor"] is None
                    else max(entry["incident_factor"], hazard.incident_factor)
                )
            entry["closed"] = entry["closed"] or is_closure

    return {segment_id: SegmentHazardContext(**values) for segment_id, values in raw.items()}
