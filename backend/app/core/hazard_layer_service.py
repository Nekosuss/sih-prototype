"""
Part 11: samples a road segment's REAL OSM geometry at multiple points
(start/quarter/mid/three-quarter/end -- app/config.py::HAZARD_SEGMENT_SAMPLE_FRACTIONS)
and queries the spatial hazard-zonation loader (app/data/hazard_layer_loader.py)
at each, aggregating conservatively into one result per segment. This is the
segment-level counterpart to that module's point-level
get_landslide_hazard()/get_flood_hazard() -- mirrors
app/core/weather_factor.py's role for Part 10's rainfall loader.

--- Why multiple points, not just the midpoint ---

road segment -> spatial hazard layer -> hazard level/score

A hazard-zonation polygon boundary can fall partway along even a short
mountain segment; trusting only the midpoint would silently miss a
genuinely hazardous stretch that happens to start or end the segment
instead. Sampling 5 points and taking the MAXIMUM real (non-missing) score
among them is the same "most conservative reading wins" principle already
used elsewhere in this codebase (core/hazard_state.py's multi-hazard
combination, routing_engine.py's ROUTE_AGGREGATE_MAX_WEIGHT) -- a segment
is reported as being as hazardous as its worst real sampled point, never
diluted by averaging with safer points elsewhere on the same segment.

A segment where every sample is out of coverage is reported no_coverage,
never coerced to a fabricated 0.0/"low".
"""
from dataclasses import dataclass
from typing import Optional

from app.config import HAZARD_SEGMENT_SAMPLE_FRACTIONS
from app.core.geo import interpolate_along_path
from app.data.hazard_layer_loader import (
    HazardLayerStatus,
    HazardLevel,
    HazardObservation,
    get_default_hazard_layer_loader,
)


@dataclass(frozen=True)
class SegmentHazardLayerResult:
    segment_id: str
    hazard_type: str  # "landslide" | "flood"
    sample_observations: list[HazardObservation]  # one per sampled point, in order along the segment
    status: HazardLayerStatus  # ok if >=1 sample had real coverage, else no_coverage
    hazard_class: Optional[str]  # source_class of whichever sample produced the max score
    hazard_level: Optional[HazardLevel]
    hazard_score: Optional[float]
    source: str


def _sample_points(segment) -> list[tuple[float, float]]:
    """[(lat, lon), ...] at each configured fraction of the segment's real
    length, walking its ACTUAL geometry (core/geo.py::interpolate_along_path
    -- the same real-polyline-walking utility Part 9's vehicle simulation
    uses), never a straight line between just the two endpoints."""
    points_latlon = [(p.lat, p.lng) for p in segment.geometry]
    return [
        interpolate_along_path(points_latlon, fraction * segment.distance_km)
        for fraction in HAZARD_SEGMENT_SAMPLE_FRACTIONS
    ]


def _aggregate(segment_id: str, hazard_type: str, source: str, observations: list[HazardObservation]) -> SegmentHazardLayerResult:
    covered = [o for o in observations if o.status == HazardLayerStatus.ok]
    if not covered:
        return SegmentHazardLayerResult(
            segment_id=segment_id, hazard_type=hazard_type, sample_observations=observations,
            status=HazardLayerStatus.no_coverage, hazard_class=None, hazard_level=None,
            hazard_score=None, source=source,
        )
    best = max(covered, key=lambda o: o.hazard_score)
    return SegmentHazardLayerResult(
        segment_id=segment_id, hazard_type=hazard_type, sample_observations=observations,
        status=HazardLayerStatus.ok, hazard_class=best.source_class, hazard_level=best.hazard_level,
        hazard_score=best.hazard_score, source=source,
    )


def segment_landslide_hazard(segment, loader=None) -> SegmentHazardLayerResult:
    loader = loader or get_default_hazard_layer_loader()
    observations = [loader.get_landslide_hazard(lat, lon) for lat, lon in _sample_points(segment)]
    return _aggregate(segment.id, "landslide", loader.landslide_layer.source_name, observations)


def segment_flood_hazard(segment, loader=None) -> SegmentHazardLayerResult:
    loader = loader or get_default_hazard_layer_loader()
    observations = [loader.get_flood_hazard(lat, lon) for lat, lon in _sample_points(segment)]
    return _aggregate(segment.id, "flood", loader.flood_layer.source_name, observations)
