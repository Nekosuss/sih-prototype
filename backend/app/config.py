# Tunable constants for the risk/routing logic. Kept in one place so the
# scoring and routing behavior stays easy to explain and adjust for a demo.
#
# Part 5 (core/risk_engine.py) computes the explainable prototype per-segment
# risk score. Part 6 (core/routing_engine.py's risk-aware routing section)
# is the first consumer of the ROUTING constants below — see that module's
# docstring for exactly how RISK_WEIGHT/HARD_UNSAFE_RISK_THRESHOLD/
# ROUTE_AGGREGATE_MAX_WEIGHT feed into route selection. Baseline routing
# (calculate_route/edge_cost) still reads none of these — it costs edges by
# travel time only, unchanged since Part 3.

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

# --- core/routing_engine.py: risk-aware routing (Part 6) ---

# Risk-aware edge cost: travel_time_min * (1 + RISK_WEIGHT * risk_score).
# Multiplicative rather than a separate additive term — this is the exact
# normalization the risk-vs-time combination needs: a bare additive
# `travel_time_min + RISK_WEIGHT * risk_score` would need its own arbitrary
# "how many minutes is 1.0 risk worth" constant, and a short risky segment
# would cost the same risk penalty as a long one despite far less real
# exposure. Multiplying travel_time_min by (1 + RISK_WEIGHT * risk_score)
# instead scales the penalty by how long you're actually on that segment,
# stays in the same "minutes-equivalent" unit baseline routing already uses
# (so it composes/sums across a path exactly like plain travel time does),
# and needs only one weight. At risk_score=0 this reduces exactly to
# baseline travel-time cost. RISK_WEIGHT=2.0 means a maximally-risky-but-
# still-under-the-hard-threshold segment (risk_score just below
# HARD_UNSAFE_RISK_THRESHOLD=0.75) costs up to ~2.5x its plain travel time —
# a strong, visible penalty without being so extreme it can make a 30-second
# risky shortcut "cost" more than an hour-long detour.
RISK_WEIGHT = 2.0

# Segments at or above this risk_score are excluded entirely from
# risk-aware routing (see routing_engine.build_risk_aware_graph) — not
# merely made more expensive.
#
# NOT set equal to RISK_LEVEL_THRESHOLDS["critical"] (0.75), even though
# that was the first instinct: TERRAIN_WEIGHT + HISTORICAL_WEIGHT = 0.70 is
# the highest risk_score reachable from real per-segment data (slope +
# GSI-matched history) ALONE, with no weather/incident context supplied.
# A 0.75 hard threshold would therefore be mathematically unreachable
# without a hypothetical weather/incident input — the exclusion mechanism
# would sit permanently dormant against today's real, static data, which
# defeats its purpose. 0.65 is chosen instead so a genuinely extreme real
# combination (e.g. a very steep segment that also has several very
# close matched historical landslides) CAN trigger hard exclusion on real
# data alone, while ordinary segments (this corridor's real maximum
# terrain+historical score today is well under this, see
# app/data/risk_engine_validation.py) are nowhere near it. Kept as its own
# named constant (rather than reading RISK_LEVEL_THRESHOLDS directly) so
# routing's hard cutoff and the risk engine's display-level thresholds can
# keep being tuned independently.
HARD_UNSAFE_RISK_THRESHOLD = 0.65

# Route-level risk aggregation (routing_engine.compute_route_risk_profile):
# aggregate_risk_score = ROUTE_AGGREGATE_MAX_WEIGHT * max_segment_risk
#                       + (1 - ROUTE_AGGREGATE_MAX_WEIGHT) * mean_segment_risk
# A plain mean would let one extremely dangerous segment get diluted away
# by many low-risk ones on a long route — exactly what section 6 of the
# Part 6 spec warns against. Weighting towards the maximum (0.7) means a
# single bad segment keeps the aggregate visibly high, while the mean
# component still lets an "everything is moderately risky" route read
# differently from an "everything is low risk except one spot" route with
# the same maximum.
ROUTE_AGGREGATE_MAX_WEIGHT = 0.7

# Suggested incident-severity -> incident_factor mapping (ARCHITECTURE.md
# section 6), exposed for callers that have a severity label but not a raw
# [0,1] factor. Originally a seam with no real caller (app/models/incident.py
# is still a stub) — Part 8's simulated landslide/road_blockage hazard
# events (app/core/hazard_state.py) are the first real caller.
INCIDENT_SEVERITY_FACTOR = {
    "minor": 0.2,
    "major": 0.5,
    "blocking": 1.0,
}

# --- Part 8: simulated hazard events (app/models/hazard.py, core/hazard_state.py) ---

# Mirrors INCIDENT_SEVERITY_FACTOR's role, for the OTHER hazard type
# (heavy_rain) that feeds weather_risk instead of incident_risk. Kept as a
# separate dict (rather than reusing INCIDENT_SEVERITY_FACTOR's numbers)
# because "blocking" severity means something different for each: a
# blocking incident (a literal physical obstruction) is a full 1.0 — see
# HAZARD_CLOSURE_TYPES below for why that type can bypass the weighted
# formula entirely — whereas even the most extreme simulated rain is still
# just an elevated weather_risk *input* to the same weighted formula
# everything else goes through, so it's capped at 0.9, not 1.0.
WEATHER_SEVERITY_FACTOR = {
    "minor": 0.3,
    "major": 0.6,
    "blocking": 0.9,
}

# Hazard types where "blocking" severity marks the affected segment(s)
# operationally CLOSED — excluded from risk-aware routing outright,
# regardless of what the weighted risk_score formula alone would produce.
# Why this exists: incident_risk only carries INCIDENT_WEIGHT (0.10) in the
# combined score (app/config.py above), so even a maximal incident_factor
# of 1.0 only ever contributes 0.10 to risk_score — nowhere near
# HARD_UNSAFE_RISK_THRESHOLD on its own. That's the right behavior for an
# *ambiguous* incident report, but wrong for a simulated event that is, by
# construction, "this road is physically blocked" — a blocked road isn't
# 10% riskier, it's unusable, independent of the segment's terrain/history.
# Plain strings (not the HazardType enum) so this file keeps its existing
# zero-import, pure-constants shape.
HAZARD_CLOSURE_TYPES = ("landslide", "road_blockage")
HAZARD_CLOSURE_SEVERITY = "blocking"

# core/reroute_service.py hysteresis margin: when a previously-recommended
# route is STILL feasible (no segment at/above HARD_UNSAFE_RISK_THRESHOLD),
# only switch to a different real alternative if its aggregate_risk_score
# is at least this much lower — not for a marginal improvement. Prevents
# flapping between two similarly-risky routes as risk hovers near a
# boundary. 0.05 is a small fraction of the full [0,1] risk_score range —
# enough to ignore rounding-level noise, small enough to still react to a
# genuinely meaningful improvement (e.g. the Part 6 Bhalukpong->Bomdila
# demo's 0.69 -> 0.53 swing is far larger than this margin).
ROUTE_CHANGE_HYSTERESIS_SCORE = 0.05

# --- Part 10: real IMD rainfall -> weather_factor (app/core/weather_factor.py) ---
#
# Thresholds are IMD's own official daily rainfall-intensity classification
# (used in IMD press releases/climate summaries), not arbitrary bucketing:
# Light 2.5-15.5mm, Moderate 15.6-64.4mm, Heavy 64.5-115.5mm, Very Heavy
# 115.6-204.4mm, Extremely Heavy >204.4mm. Reusing IMD's own boundaries
# means "moderate" or "heavy" here means the same thing a real IMD bulletin
# means, rather than an invented scale.
RAINFALL_LOW_MM = 2.5
RAINFALL_MODERATE_MM = 15.6
RAINFALL_HEAVY_MM = 64.5
RAINFALL_EXTREME_MM = 204.4

# weather_factor anchor values at each threshold above (piecewise LINEAR
# interpolation between consecutive anchors -- see
# weather_factor.rainfall_mm_to_weather_factor). 0mm -> 0.0 always. Below
# RAINFALL_LOW_MM, factor ramps from 0.0 up to RAINFALL_FACTOR_AT_LOW --
# real but not-yet-actionable drizzle should read as a small nonzero signal,
# not nothing. RAINFALL_FACTOR_AT_EXTREME is exactly 1.0 -- the interpolated
# curve reaches the ceiling of the [0,1] range precisely at
# RAINFALL_EXTREME_MM (IMD's own top category boundary) with no further
# gradation beyond it, so rainfall_mm_to_weather_factor stays continuous
# (no jump) right at that threshold; anything at or beyond it is clamped to
# the same 1.0. These are demo-legible, monotonically increasing anchor
# choices, not a calibrated fit against any ground-truth disruption outcome
# (none exists yet -- same caveat as every other weight in this file).
RAINFALL_FACTOR_AT_LOW = 0.10
RAINFALL_FACTOR_AT_MODERATE = 0.30
RAINFALL_FACTOR_AT_HEAVY = 0.60
RAINFALL_FACTOR_AT_EXTREME = 1.0

# The historical IMD observation date used by default wherever a caller asks
# for real rainfall without specifying one (app/api/routes_weather.py) --
# NEVER the machine's current date (Part 10 section 7: no forecasting, no
# wall-clock dependency). 2023-06-21 is a genuine monsoon day confirmed, by
# the real extracted corridor dataset itself, to be the single highest
# corridor-wide daily rainfall in the one extracted year (2023) -- see
# app/data/rainfall_validation.py. Chosen for demo legibility (a real day
# where the weather component is actually visible), not fabricated.
DEFAULT_RAINFALL_OBSERVATION_DATE = "2023-06-21"

# --- Part 9: vehicle/GPS simulation (app/simulation/vehicle_simulator.py) ---

# A DETERMINISTIC SIMULATED speed, not a measured/real vehicle speed. Used
# to convert real elapsed wall-clock time into real distance travelled
# along a route's ACTUAL geometry (see core/geo.py::interpolate_along_path)
# — there is no physics, acceleration, or traffic model, deliberately: the
# goal is demonstration reliability (the same elapsed time always produces
# the same position), not realistic vehicle dynamics. 60 km/h is a simple,
# round, easy-to-narrate-in-a-demo default for this corridor's mix of
# highway and mountain-road segments; each Vehicle can override it
# per-instance (see Vehicle.speed_kmph) without changing this default.
SIMULATION_SPEED_KMPH = 60.0

# --- Part 11: landslide/flood HAZARD ZONATION layers (app/data/hazard_layer_loader.py) ---
#
# Distinct from HISTORICAL_COUNT_REFERENCE etc. above, which govern the GSI
# OBSERVED-event component. This section governs SUSCEPTIBILITY/ZONATION
# ("which areas are more prone"), a separate concept -- see
# app/data/hazard_layer_loader.py's module docstring for the full
# distinction and this project's verified data-access status against the
# primary official source (APSAC/SRSAC).

# Generic source-class -> normalized [0,1] score mapping, case-insensitive.
# This is the STANDARD five-class hazard-zonation vocabulary used across
# Indian landslide/flood hazard mapping practice (NDMA/BIS guidance uses
# exactly Very Low/Low/Moderate/High/Very High) -- NOT an APSAC-specific
# invented scale, and NOT fitted to any real APSAC file (none has been
# obtained -- see hazard_layer_loader.py). If/when a real official layer is
# dropped in and its attribute column uses different class strings, update
# this table to match that file's actual vocabulary rather than relabelling
# its data to fit this default.
HAZARD_CLASS_TO_SCORE = {
    "very low": 0.10,
    "low": 0.25,
    "moderate": 0.45,
    "medium": 0.45,  # alias some sources use instead of "moderate"
    "high": 0.70,
    "very high": 0.90,
    "severe": 0.90,  # alias sometimes used for flood-hazard's top class
}

# Normalized [0,1] hazard_score -> display HazardLevel bucket. Mirrors
# RISK_LEVEL_THRESHOLDS' quartile-style convention above, per Part 11's own
# design spec (LOW 0.0-0.25 / MODERATE 0.25-0.50 / HIGH 0.50-0.75 /
# VERY_HIGH 0.75-1.00) -- a display convenience, not a claim that these
# quartiles correspond to any official APSAC classification boundary.
HAZARD_LEVEL_THRESHOLDS = {
    "low": 0.0,
    "moderate": 0.25,
    "high": 0.5,
    "very_high": 0.75,
}

# --- Part 12: field reporting / incident intelligence ---
#
# A field report's raw GPS coordinates are matched to the nearest REAL OSM
# road segment (core/geo.py::nearest_point_on_polyline via
# core/field_report_service.py). This is the maximum distance from that
# nearest segment a report may still be accepted at -- beyond it, the report
# is rejected rather than silently snapped onto a distant road (see
# core/field_report_service.py::NoNearbyRoadError). 300m is a generous but
# bounded prototype tolerance for handheld-GPS error (tens of meters) plus a
# mountain road's own width/verge, while still refusing a report that is
# clearly nowhere near this corridor's road network (e.g. a wrong-country
# coordinate). Not derived from a real GPS-accuracy study -- a documented,
# tunable prototype default, exactly like every other constant in this file.
FIELD_REPORT_MAX_SNAP_DISTANCE_M = 300.0

# core/field_report_service.py::is_possible_duplicate -- an ACTIVE field
# report of the SAME incident type on the SAME matched segment, submitted
# within this many minutes of an existing one, is flagged
# possible_duplicate=True on the NEW report. Never causes either report to
# be discarded or merged (Part 12 section 13) -- both are always kept as
# independent records with independent HazardEvents.
FIELD_REPORT_DUPLICATE_WINDOW_MINUTES = 60

# Field-report incident type -> underlying app/models/hazard.py HazardType.
# FieldIncidentType (app/models/field_report.py) is a broader real-world
# reporting vocabulary than HazardType's small demo-simulation one
# (heavy_rain/landslide/road_blockage) -- rather than inventing a second
# severity/factor mapping for the extra categories, every field incident
# type EXCEPT landslide maps onto "road_blockage": a fallen tree, an
# accident, flood damage, general road damage, etc. are all -- for this
# prototype's routing purposes -- "this road is operationally obstructed",
# the same category road_blockage already represents. This is why a
# "blocking" field report of ANY of these types closes its segment exactly
# like a simulated blocking road_blockage hazard does (see
# HAZARD_CLOSURE_TYPES/HAZARD_CLOSURE_SEVERITY above). The severity->factor
# NUMBERS themselves are still INCIDENT_SEVERITY_FACTOR above, unchanged and
# never duplicated -- this table only maps a category label, nothing more.
FIELD_REPORT_INCIDENT_TO_HAZARD_TYPE = {
    "landslide": "landslide",
    "road_blockage": "road_blockage",
    "flooding": "road_blockage",
    "accident": "road_blockage",
    "fallen_tree": "road_blockage",
    "damaged_road": "road_blockage",
    "other": "road_blockage",
}

# Fractions along a road segment's REAL geometry to sample when querying a
# spatial hazard-zonation layer (app/core/hazard_layer_service.py) --
# start/quarter/mid/three-quarter/end, per Part 11 spec section 4. A
# hazard-zonation polygon boundary can fall partway along even a short
# mountain segment; sampling multiple points and taking the conservative
# MAXIMUM across them (see hazard_layer_service.py) avoids the false
# precision of trusting only the midpoint, the same reasoning
# ROUTE_AGGREGATE_MAX_WEIGHT already applies at the route level above.
HAZARD_SEGMENT_SAMPLE_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
