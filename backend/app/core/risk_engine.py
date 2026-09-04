"""
Risk-scoring surface. Two generations of logic live here side by side:

1. compute_base_risk() / get_risk_summary() — the Part 2 terrain-only
   static score. Still used by osm_geojson_loader.py to populate
   RoadSegment.base_risk/current_risk_score at load time, and by
   api/routes_network.py's GET /segments/{id}/risk. Left UNCHANGED by
   Part 5 — nothing here rewrites those fields or that endpoint.

2. assess_segment_risk() — the Part 5 EXPLAINABLE PROTOTYPE risk engine.
   This is a new, separate, richer scorer that a caller invokes explicitly
   (it is not wired into RoadSegment.current_risk_score, routing, or any
   API route — see below for why). It uses the real data this corridor now
   actually has (Part 4.8's DEM-derived slope_deg, the GSI->OSM spatial
   join's historical_landslide_count/nearest_landslide_distance_m) instead
   of the Part 2 placeholder landslide_susceptibility/flood_susceptibility
   fields (which remain uniformly 0.0 — no real hazard-zonation dataset
   exists yet, see backend/app/data/README.md).

--- What "explainable prototype" means here, precisely ---

    risk_score = clamp(
        TERRAIN_WEIGHT    * slope_risk
      + HISTORICAL_WEIGHT * historical_landslide_risk
      + WEATHER_WEIGHT    * weather_risk
      + INCIDENT_WEIGHT   * incident_risk,
      0, 1)

Every constant above lives in app/config.py, documented there with the
reasoning behind each choice. This is a **rule-based weighted combination**,
not a trained model — there is no historical rainfall time series and no
event-date-aligned landslide labels to fit or calibrate anything against
yet (see backend/app/data/training_dataset_schema.md, Part 4.7, for exactly
what's missing). Every RiskResult carries risk_score plus the unweighted
per-component breakdown and a plain-language `reasons` list — the entire
point of "explainable" is that nothing here is a black box.

**Never call risk_score a probability.** Nothing in this module or its
callers should render text like "73% chance of landslide" — say "prototype
risk score: 0.73" instead. See RiskResult.methodology_note.

--- Why this DOESN'T touch RoadSegment.current_risk_score or routing ---

routing_engine.edge_cost() only ever reads travel_time_min (Part 3
baseline, unchanged). Wiring risk into routing cost, rerouting, or
PROCEED/REROUTE/SUSPEND decisions is explicitly later scope (Part 5 is the
risk engine only) — see ARCHITECTURE.md section 4 for the eventual
`edge_cost = distance_km * (1 + RISK_WEIGHT * segment_risk)` design this is
a deliberate, documented step towards, not yet.

--- Component methodology ---

slope_risk: normalizes the real DEM-derived RoadSegment.slope_deg (Part
4.8 — backend/app/data/README.md "DEM provenance") linearly between
SLOPE_RISK_ZERO_DEG and SLOPE_RISK_SATURATION_DEG. Slope is a TERRAIN
FEATURE, not itself a landslide probability — a steep segment is more
exposed to slope-driven hazards in general, which is exactly what a
terrain-risk component in an explainable rule-based score is for, but
steepness alone does not mean a landslide is likely at any given moment.

historical_landslide_risk: a log-scaled transform of
RoadSegment.historical_landslide_count (real GSI observations spatially
matched within landslide_mapper.py's 500m threshold), blended with a
proximity score from nearest_landslide_distance_m. See app/config.py for
the exact constants and reasoning. **Critical caveat, repeated in every
RiskResult's `reasons`**: only 104 of the corridor's ~2,964 segments have
any matched GSI observation. historical_landslide_count == 0 means "no
matched historical record in the current dataset," never "confirmed safe."

Part 11 addition: this component ALSO folds in
RoadSegment.landslide_hazard_score (an official landslide hazard-ZONATION
layer, "which areas are more prone" -- a different concept from the GSI
HISTORICAL inventory above, "where landslides have been observed"; see
app/data/hazard_layer_loader.py). The two are combined via MAX, not sum:
they are correlated evidence for the SAME hazard (landslides), so adding
their weights would double-count a segment where both real signals agree,
while MAX reports whichever real signal is currently worse without
inventing a statistically fused number neither source was calibrated
against. This stays under the existing HISTORICAL_WEIGHT — no new weight
was introduced, and RiskBreakdown's `historical_landslide_risk` field name
is unchanged (renaming a public field would be a bigger compatibility break
than documenting its slightly broadened meaning here). When
landslide_hazard_score is None (its default, and — as of Part 11's
delivery — its value for every real segment in this corridor, since no
official APSAC zonation layer has actually been obtained; see
hazard_layer_loader.py's module docstring), this reduces EXACTLY to the
pre-Part-11 formula: see
tests/test_hazard_layer.py::test_historical_landslide_risk_unchanged_when_no_hazard_layer.

flood_hazard_score (also Part 11) is deliberately NOT folded into
risk_score at all yet — see FLOOD_HAZARD_NOTE below.

weather_risk: an externally supplied `weather_factor` in [0,1], defaulting
to 0.0 when not supplied. This is a CURRENT-CONTEXT INPUT (e.g. a future
live weather integration, or a manual demo control), not a trained
rainfall-based prediction — no historical rainfall dataset is integrated
in this part (that's explicitly later scope). A default of 0.0 must be
read as "no weather signal was supplied," not "clear weather confirmed."

incident_risk: an externally supplied `incident_factor` in [0,1], same
default-to-0.0 behaviour. No real Incident model/reporting pipeline exists
yet (app/models/incident.py is still a stub) — incident_factor_from_severity()
below is the seam a future one plugs into, mapping the severity labels
already sketched in ARCHITECTURE.md section 6 (minor/major/blocking) to a
factor, without building the rest of that workflow now.

--- FLOOD_HAZARD_NOTE (Part 11) ---

RoadSegment.flood_hazard_class/flood_hazard_score (an official flood
hazard-zonation layer — see app/data/hazard_layer_loader.py) are
deliberately NOT read anywhere in this module, unlike landslide_hazard_score
(folded into historical_landslide_risk above). The existing formula has no
flood-shaped slot to fold it into without either (a) introducing a new
weight — which would mean shrinking an existing one, i.e. quietly
recalibrating a formula this project has repeatedly been told not to
casually change — or (b) blending it into an unrelated component (slope,
weather) in a way that would misrepresent what that component's existing
public field name means. Flood hazard is therefore exposed as
INFORMATIONAL segment/API data only (GET /hazards/segments/{id}) for now —
the same honest, pre-scoring state `flood_susceptibility` has held since
Part 2 — rather than fabricating a calibration for a component with zero
real official data behind it today. It remains a clean seam for a future,
deliberate weighting decision once real APSAC flood-zonation data exists.
"""
import math
from typing import Optional

from app.config import (
    HISTORICAL_COUNT_REFERENCE,
    HISTORICAL_COUNT_VS_PROXIMITY_WEIGHT,
    HISTORICAL_PROXIMITY_MAX_M,
    HISTORICAL_WEIGHT,
    INCIDENT_SEVERITY_FACTOR,
    INCIDENT_WEIGHT,
    RISK_LEVEL_THRESHOLDS,
    SLOPE_RISK_SATURATION_DEG,
    SLOPE_RISK_ZERO_DEG,
    TERRAIN_WEIGHT,
    WEATHER_WEIGHT,
)
from app.models.network import RoadSegment, TerrainType
from app.models.risk import RiskBreakdown, RiskLevel, RiskResult

# ---------------------------------------------------------------------------
# Part 2: static terrain-only base risk (UNCHANGED — see module docstring)
# ---------------------------------------------------------------------------

_TERRAIN_BASE_RISK = {
    TerrainType.plain: 0.05,
    TerrainType.hill: 0.15,
    TerrainType.mountain: 0.25,
}


def compute_base_risk(
    terrain_type: TerrainType,
    landslide_susceptibility: float,
    flood_susceptibility: float,
) -> float:
    raw = _TERRAIN_BASE_RISK[terrain_type] + 0.4 * landslide_susceptibility + 0.2 * flood_susceptibility
    return round(min(1.0, raw), 3)


def get_risk_summary(segment: RoadSegment) -> dict:
    """Shape of the 'current segment status/risk' API response (Part 2/3,
    unchanged by Part 5). See assess_segment_risk() below for the newer,
    richer explainable prototype score — not yet wired into this endpoint."""
    return {
        "segment_id": segment.id,
        "status": segment.status,
        "base_risk": segment.base_risk,
        "current_risk_score": segment.current_risk_score,
        "terrain_type": segment.terrain_type,
        "landslide_susceptibility": segment.landslide_susceptibility,
        "flood_susceptibility": segment.flood_susceptibility,
    }


# ---------------------------------------------------------------------------
# Part 5: explainable prototype risk engine
# ---------------------------------------------------------------------------


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def slope_risk(segment: RoadSegment) -> float:
    """Real DEM slope_deg -> [0,1]. None (should be rare post-Part 4.8,
    but not impossible — see dem_processor.py's fallback path) is treated
    as 0 contribution, not fabricated: absence of a real measurement must
    never silently read as "flat and safe" to a human, so callers should
    check RoadSegment.slope_deg is not None before trusting a 0 here as
    "actually flat" rather than "no data.\""""
    if segment.slope_deg is None:
        return 0.0
    if segment.slope_deg <= SLOPE_RISK_ZERO_DEG:
        return 0.0
    if segment.slope_deg >= SLOPE_RISK_SATURATION_DEG:
        return 1.0
    return (segment.slope_deg - SLOPE_RISK_ZERO_DEG) / (SLOPE_RISK_SATURATION_DEG - SLOPE_RISK_ZERO_DEG)


def historical_landslide_risk(segment: RoadSegment) -> float:
    """Real GSI-matched historical_landslide_count/nearest_landslide_distance_m
    -> [0,1], blended (Part 11) with RoadSegment.landslide_hazard_score (an
    official landslide hazard-ZONATION layer, when available — see module
    docstring's "Part 11 addition" paragraph above for the full reasoning).
    count == 0 and landslide_hazard_score == None both contribute 0.0 here
    — deliberately: this function only reports what real matched/zoned data
    says, it never guesses at unobserved hazard. The "0 is not proof of
    safety" caveat belongs in the *explanation* layer (_build_reasons
    below), not silently baked into a nonzero score."""
    count = segment.historical_landslide_count
    if count <= 0:
        history_score = 0.0
    else:
        count_score = min(1.0, math.log1p(count) / math.log1p(HISTORICAL_COUNT_REFERENCE))

        if segment.nearest_landslide_distance_m is not None:
            proximity_score = _clamp01(1.0 - segment.nearest_landslide_distance_m / HISTORICAL_PROXIMITY_MAX_M)
        else:
            proximity_score = 0.0

        history_score = _clamp01(
            HISTORICAL_COUNT_VS_PROXIMITY_WEIGHT * count_score
            + (1.0 - HISTORICAL_COUNT_VS_PROXIMITY_WEIGHT) * proximity_score
        )

    susceptibility_score = segment.landslide_hazard_score if segment.landslide_hazard_score is not None else 0.0
    # MAX, not sum — see the module docstring's Part 11 paragraph. When
    # landslide_hazard_score is None, susceptibility_score is 0.0, which
    # never exceeds history_score (already clamped to [0,1] and >= 0), so
    # this line is then a no-op and the pre-Part-11 value is returned
    # unchanged.
    return max(history_score, susceptibility_score)


def weather_risk(weather_factor: Optional[float]) -> float:
    """External current-context input, NOT a trained rainfall prediction.
    None (not supplied) -> 0.0, meaning "no weather signal available,"
    never "confirmed clear.\""""
    if weather_factor is None:
        return 0.0
    return _clamp01(weather_factor)


def incident_risk(incident_factor: Optional[float]) -> float:
    """External current-context input. None (not supplied) -> 0.0, meaning
    "no active incident reported," which — like historical count — reflects
    reporting coverage, not a confirmed all-clear."""
    if incident_factor is None:
        return 0.0
    return _clamp01(incident_factor)


def incident_factor_from_severity(severity: str) -> float:
    """Maps the severity labels sketched in ARCHITECTURE.md section 6
    (minor/major/blocking) to an incident_factor. Raises on an unknown
    label rather than guessing — see INCIDENT_SEVERITY_FACTOR in
    app/config.py. No real Incident model reads this yet; it exists so a
    future one has a ready seam."""
    try:
        return INCIDENT_SEVERITY_FACTOR[severity]
    except KeyError:
        raise ValueError(
            f"Unknown incident severity {severity!r}; expected one of {sorted(INCIDENT_SEVERITY_FACTOR)}"
        ) from None


def _risk_level(score: float) -> RiskLevel:
    if score >= RISK_LEVEL_THRESHOLDS["critical"]:
        return RiskLevel.critical
    if score >= RISK_LEVEL_THRESHOLDS["high"]:
        return RiskLevel.high
    if score >= RISK_LEVEL_THRESHOLDS["moderate"]:
        return RiskLevel.moderate
    return RiskLevel.low


def _build_reasons(
    segment: RoadSegment,
    breakdown: RiskBreakdown,
    weather_factor: Optional[float],
    incident_factor: Optional[float],
    risk_score: float,
) -> list[str]:
    reasons: list[str] = []

    if segment.slope_deg is None:
        reasons.append("Slope data unavailable for this segment (no terrain contribution applied)")
    elif breakdown.slope_risk >= 0.5:
        reasons.append(f"High slope exposure ({segment.slope_deg:.1f} deg, real SRTM DEM-derived)")
    elif breakdown.slope_risk > 0.0:
        reasons.append(f"Moderate slope exposure ({segment.slope_deg:.1f} deg, real SRTM DEM-derived)")
    else:
        reasons.append(f"Low slope exposure ({segment.slope_deg:.1f} deg)")

    if segment.historical_landslide_count > 0:
        distance_note = (
            f", nearest {segment.nearest_landslide_distance_m:.0f}m away"
            if segment.nearest_landslide_distance_m is not None
            else ""
        )
        plural = "observation" if segment.historical_landslide_count == 1 else "observations"
        reasons.append(
            f"{segment.historical_landslide_count} historical GSI landslide {plural} "
            f"matched to this segment{distance_note}"
        )
    else:
        reasons.append(
            "No matched historical GSI landslide observation for this segment in the "
            "current 104-record corridor dataset -- this reflects data coverage, not a "
            "confirmed absence of hazard"
        )

    if segment.landslide_hazard_score is not None:
        reasons.append(
            f"Landslide hazard zonation layer: {segment.landslide_hazard_class} "
            f"(normalized score={segment.landslide_hazard_score:.2f}, "
            f"source={segment.hazard_layer_source.get('landslide_hazard', 'unknown')})"
        )
    else:
        reasons.append(
            "No official landslide hazard-zonation layer available for this segment "
            "(APSAC data not locally obtainable -- see backend/app/data/hazard_layer_loader.py); "
            "historical GSI evidence only"
        )

    if breakdown.weather_risk > 0.0:
        reasons.append(f"Elevated current weather risk supplied (factor={weather_factor:.2f})")
    elif weather_factor is None:
        reasons.append("No current weather context supplied (not the same as confirmed clear weather)")

    if breakdown.incident_risk > 0.0:
        reasons.append(f"Active field incident contribution supplied (factor={incident_factor:.2f})")

    reasons.append(f"Prototype risk score: {risk_score:.2f} (explainable rule-based estimate, not a calibrated probability)")
    return reasons


def assess_segment_risk(
    segment: RoadSegment,
    weather_factor: Optional[float] = None,
    incident_factor: Optional[float] = None,
) -> RiskResult:
    """
    The Part 5 explainable prototype risk engine's single entry point.

    weather_factor / incident_factor: optional externally supplied [0,1]
    current-context inputs (see module docstring). Both default to None
    (no signal supplied), which contributes 0.0 to the score — never
    fabricated, never read as "confirmed safe."

    Returns a RiskResult with the clamped [0,1] risk_score, a risk_level,
    the unweighted per-component breakdown, and a plain-language reasons
    list explaining the score. Pure function: does not read or write
    StateStore, does not touch RoadSegment.current_risk_score, and has no
    routing side effects.
    """
    sr = slope_risk(segment)
    hr = historical_landslide_risk(segment)
    wr = weather_risk(weather_factor)
    ir = incident_risk(incident_factor)

    breakdown = RiskBreakdown(
        slope_risk=round(sr, 4),
        historical_landslide_risk=round(hr, 4),
        weather_risk=round(wr, 4),
        incident_risk=round(ir, 4),
    )

    raw_score = TERRAIN_WEIGHT * sr + HISTORICAL_WEIGHT * hr + WEATHER_WEIGHT * wr + INCIDENT_WEIGHT * ir
    risk_score = round(_clamp01(raw_score), 4)
    risk_level = _risk_level(risk_score)
    reasons = _build_reasons(segment, breakdown, weather_factor, incident_factor, risk_score)

    return RiskResult(
        segment_id=segment.id,
        risk_score=risk_score,
        risk_level=risk_level,
        breakdown=breakdown,
        reasons=reasons,
        metadata={
            "slope_deg": "" if segment.slope_deg is None else f"{segment.slope_deg}",
            "elevation_m": "" if segment.elevation_m is None else f"{segment.elevation_m}",
            "historical_landslide_count": str(segment.historical_landslide_count),
            "nearest_landslide_distance_m": (
                "" if segment.nearest_landslide_distance_m is None else f"{segment.nearest_landslide_distance_m}"
            ),
            "weather_factor_supplied": str(weather_factor is not None),
            "incident_factor_supplied": str(incident_factor is not None),
            "landslide_hazard_class": segment.landslide_hazard_class or "",
            "landslide_hazard_score": "" if segment.landslide_hazard_score is None else f"{segment.landslide_hazard_score}",
            "flood_hazard_class": segment.flood_hazard_class or "",
            "flood_hazard_score": "" if segment.flood_hazard_score is None else f"{segment.flood_hazard_score}",
        },
    )
