# Tunable constants for the risk/routing logic. Kept in one place so the
# scoring and routing behavior stays easy to explain and adjust for a demo.
#
# Part 5 (core/risk_engine.py) is the first real consumer — see that
# module's docstring for exactly how each constant below feeds into the
# explainable prototype risk score. Nothing in core/routing_engine.py reads
# any of these yet (risk-aware routing is a later part) — RISK_WEIGHT is
# deliberately not defined here until that part exists, so it can't be
# imported and accidentally half-wired in early.

# --- core/risk_engine.py: explainable prototype risk score ---

# Combination weights for risk_score = clamp(sum(weight * component), 0, 1).
# Sum to 1.0 so a combination of in-range [0,1] components never exceeds 1
# on its own; clamping is still applied defensively. TERRAIN and HISTORICAL
# get equal, dominant weight because real DEM slope and real GSI-matched
# landslide history are this corridor's only two genuinely real,
# segment-level hazard signals today. WEATHER gets a moderate weight: it's
# a legitimate real-time modifier in principle, but today it's only ever an
# externally supplied factor (see WeatherCondition note below) — there is
# no historical-rainfall-driven estimate behind it yet. INCIDENT gets the
# smallest baseline weight because no real incident-reporting pipeline
# exists yet (app/models/incident.py is still a stub) — see
# risk_engine.py's module docstring for the limitation this implies (a
# single active "blocking" incident does not, by itself, push a score to
# CRITICAL under this simple weighted-sum model; a future revision may want
# certain incident types to override/dominate rather than just be weighted).
TERRAIN_WEIGHT = 0.35
HISTORICAL_WEIGHT = 0.35
WEATHER_WEIGHT = 0.20
INCIDENT_WEIGHT = 0.10

# Slope-risk normalization (real DEM slope_deg -> [0,1]).
# Below SLOPE_RISK_ZERO_DEG a road is treated as essentially flat (no
# terrain risk contribution). At/above SLOPE_RISK_SATURATION_DEG the
# contribution saturates at 1.0 — chosen to match the "suspicious slope"
# threshold already used in app/data/dem_validation.py (Part 4.8) for a
# sustained steep road grade on this corridor, so both parts of the
# pipeline agree on what "very steep" means here. Linear in between.
SLOPE_RISK_ZERO_DEG = 2.0
SLOPE_RISK_SATURATION_DEG = 25.0

# Historical-landslide-evidence normalization.
# A log transform means a segment with many matched observations does not
# get proportionally many times the risk contribution of a segment with
# just one — the marginal value of each additional observation shrinks.
# This specifically prevents the single most-observed segment in the
# current 104-record GSI match set (max 11 matched observations on one
# segment) from swamping the whole score's scale.
# HISTORICAL_COUNT_REFERENCE is the count at which the log-scaled
# count-component saturates at 1.0 — deliberately well below the current
# real maximum (11), so "a lot of history" segments are treated similarly
# rather than the single most-observed segment dominating the scale.
HISTORICAL_COUNT_REFERENCE = 5
# Within the historical component, blend the count-based score with how
# close the nearest matched observation actually is: 0m away -> full
# proximity weight, at/beyond the spatial join's own match threshold -> no
# proximity boost. HISTORICAL_PROXIMITY_MAX_M intentionally matches
# landslide_mapper.DEFAULT_MATCH_THRESHOLD_M (the distance beyond which a
# GSI record isn't even considered matched to a segment) so this doesn't
# introduce a second, inconsistent notion of "close."
HISTORICAL_PROXIMITY_MAX_M = 500.0
# Weight given to the count-based score in that blend; the remainder
# (1 - this) goes to the proximity-based score. Count gets the larger share
# because it is the less noisy of the two signals (the GSI extraction's
# lat/lng precision varies record to record — see
# backend/app/data/README.md — so exact distance is a slightly softer
# signal than "how many independent reports exist here at all").
HISTORICAL_COUNT_VS_PROXIMITY_WEIGHT = 0.7

# Risk levels: thresholds on the final clamped [0,1] risk_score. An even
# quartile-style split, chosen for a demo-legible prototype scale — easy to
# retune once real calibration data (see
# backend/app/data/training_dataset_schema.md) exists.
RISK_LEVEL_THRESHOLDS = {
    "low": 0.0,
    "moderate": 0.25,
    "high": 0.5,
    "critical": 0.75,
}

# Suggested incident-severity -> incident_factor mapping (ARCHITECTURE.md
# section 6), exposed for callers that have a severity label but not a raw
# [0,1] factor. No real Incident model or field-reporting pipeline exists
# yet (app/models/incident.py is still a stub) — this is only the seam a
# future one plugs into; nothing in this codebase parses real "minor" /
# "major" / "blocking" reports yet.
INCIDENT_SEVERITY_FACTOR = {
    "minor": 0.2,
    "major": 0.5,
    "blocking": 1.0,
}
