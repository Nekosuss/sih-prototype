"""
Part 10: converts a real IMD daily rainfall observation
(app/data/rainfall_loader.py) into the weather_factor in [0,1] that Part 5's
risk engine (core/risk_engine.py::assess_segment_risk) already accepts as an
external current-context input, and resolves a per-segment real-rainfall
SegmentHazardContext the exact same way Part 8's simulated hazards do
(core/hazard_state.py). No new risk formula is introduced here -- this
module only produces the ONE new input the existing formula was always
designed to accept, from a real rainfall source instead of a manual/
simulated one.

--- rainfall_mm -> weather_factor ---

Deterministic PIECEWISE LINEAR interpolation between the anchors in
app/config.py (RAINFALL_LOW_MM/MODERATE_MM/HEAVY_MM/EXTREME_MM and their
matching RAINFALL_FACTOR_AT_* values), which are IMD's own official daily
rainfall-intensity category boundaries -- not an arbitrary bucketing, and
not a trained/fitted model. 0mm -> 0.0. At/above RAINFALL_EXTREME_MM,
weather_factor saturates at 1.0. This is explicitly NOT a probability of a
landslide/disruption -- see risk_engine.py's module docstring for the same
caveat applied to every component of the combined risk_score.

--- What "missing" means here ---

rainfall_mm_to_weather_factor(None) -> None ("no real rainfall signal
available for this point/date"), which risk_engine.weather_risk() already
treats as "no signal supplied" (defaults its contribution to 0.0) -- the
SAME semantics the existing manual/hazard-driven weather_factor already
has. This module never invents a 0.0 weather_factor for a missing
observation; that would misrepresent absence of data as confirmed dry
weather.
"""
from dataclasses import dataclass
from typing import Optional

from app.config import (
    RAINFALL_EXTREME_MM,
    RAINFALL_FACTOR_AT_EXTREME,
    RAINFALL_FACTOR_AT_HEAVY,
    RAINFALL_FACTOR_AT_LOW,
    RAINFALL_FACTOR_AT_MODERATE,
    RAINFALL_HEAVY_MM,
    RAINFALL_LOW_MM,
    RAINFALL_MODERATE_MM,
)
from app.core.hazard_state import SegmentHazardContext
from app.data.rainfall_loader import RainfallObservation, RainfallStatus, get_default_rainfall_loader

# (rainfall_mm, weather_factor) anchor points, in increasing order. The
# piecewise-linear interpolation below walks these pairs; the choice of
# thresholds/factors is documented in app/config.py.
_ANCHORS: list[tuple[float, float]] = [
    (0.0, 0.0),
    (RAINFALL_LOW_MM, RAINFALL_FACTOR_AT_LOW),
    (RAINFALL_MODERATE_MM, RAINFALL_FACTOR_AT_MODERATE),
    (RAINFALL_HEAVY_MM, RAINFALL_FACTOR_AT_HEAVY),
    (RAINFALL_EXTREME_MM, RAINFALL_FACTOR_AT_EXTREME),
]


def rainfall_mm_to_weather_factor(rainfall_mm: Optional[float]) -> Optional[float]:
    """Deterministic, monotonically non-decreasing piecewise-linear mapping.
    None in -> None out (see module docstring). A negative rainfall_mm is
    not physically meaningful -- IMD's own no-data sentinel is handled
    upstream by rainfall_loader.py, never passed in here as a raw number."""
    if rainfall_mm is None:
        return None
    if rainfall_mm <= 0.0:
        return 0.0
    if rainfall_mm >= RAINFALL_EXTREME_MM:
        return 1.0

    for (lo_mm, lo_factor), (hi_mm, hi_factor) in zip(_ANCHORS, _ANCHORS[1:]):
        if lo_mm <= rainfall_mm <= hi_mm:
            fraction = (rainfall_mm - lo_mm) / (hi_mm - lo_mm)
            return round(lo_factor + fraction * (hi_factor - lo_factor), 4)

    # Unreachable given the >= RAINFALL_EXTREME_MM check above, kept only as
    # an explicit, honest fallback rather than an unexplained silent path.
    return 1.0


@dataclass(frozen=True)
class SegmentWeatherFactor:
    """The full real-rainfall chain for one segment/point, kept together so
    an API response or validation report can show every step (rainfall ->
    weather_factor) rather than just the final number."""

    observation: RainfallObservation
    weather_factor: Optional[float]  # None whenever observation.status != ok


def weather_factor_for_point(lat: float, lon: float, date, loader=None) -> SegmentWeatherFactor:
    """The Part 10 entry point for an arbitrary coordinate (see
    app/api/routes_weather.py's GET /weather/rainfall)."""
    loader = loader or get_default_rainfall_loader()
    observation = loader.get_daily_rainfall(lat, lon, date)
    factor = (
        rainfall_mm_to_weather_factor(observation.rainfall_mm)
        if observation.status == RainfallStatus.ok
        else None
    )
    return SegmentWeatherFactor(observation=observation, weather_factor=factor)


def weather_factor_for_segment(segment, date, loader=None) -> SegmentWeatherFactor:
    """Segment-level version: uses the segment's real OSM geometry midpoint
    as its representative coordinate (the SAME convention already used
    elsewhere in this codebase for "one representative point per segment" --
    see dem_validation.py::_nearest_segment and
    risk_engine_validation.py). A 0.25 deg (~25-28km) IMD grid cell is
    obviously coarser than any single road segment; this is grid-cell
    rainfall assigned to the segment's representative location, not a
    road-level rainfall measurement -- see rainfall_loader.py's module
    docstring for the coverage caveat this inherits."""
    representative_point = segment.geometry[len(segment.geometry) // 2]
    return weather_factor_for_point(representative_point.lat, representative_point.lng, date, loader=loader)


def rainfall_segment_context(segments, date, loader=None) -> dict[str, SegmentHazardContext]:
    """Builds a per-segment SegmentHazardContext (the exact Part 8 type
    core/routing_engine.py and core/reroute_service.py already accept as
    `segment_context`) from real rainfall, for every segment whose
    representative point has a real (status == ok) observation for `date`.
    A segment with no real observation for this date/location is simply
    OMITTED from the returned dict -- exactly like an inactive/absent
    hazard is omitted in combine_active_hazards_into_segment_context() --
    so routing/risk code that reads `segment_context.get(segment_id)` sees
    "no override" rather than a fabricated weather_factor of 0.0.

    incident_factor/closed are never set here -- real rainfall only ever
    feeds weather_risk (see risk_engine.py), the same restriction the
    heavy_rain hazard type already has (core/hazard_state.py).
    """
    loader = loader or get_default_rainfall_loader()
    context: dict[str, SegmentHazardContext] = {}
    for segment in segments:
        result = weather_factor_for_segment(segment, date, loader=loader)
        if result.weather_factor is None:
            continue
        context[segment.id] = SegmentHazardContext(weather_factor=result.weather_factor)
    return context
