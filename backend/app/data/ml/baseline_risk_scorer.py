"""
Part 14.3: the "existing explainable risk features" baseline (Step 3),
built by calling the REAL, UNMODIFIED production risk engine
(app/core/risk_engine.py::assess_segment_risk) once per segment-year row --
never a reimplementation, never a modified copy of its formula/weights.

--- How a per-year score is produced without touching core/risk_engine.py ---

assess_segment_risk() reads `historical_landslide_count` /
`nearest_landslide_distance_m` directly off the RoadSegment object it's
given. The real, live RoadSegment objects carry the STATIC, all-time count
(no year cutoff) -- using them as-is for, say, a 2016 row would leak the
2021 event backward in time. This module never mutates or monkeypatches
risk_engine.py; instead, for each row it builds a temporary
`segment.model_copy(update=...)` with ONLY those two fields swapped for
this row's own prior-cutoff values (already computed with a strict
year-cutoff by build_segment_year_dataset.py) -- slope_deg/elevation_m/
terrain_type/landslide_hazard_score are real, time-invariant static
features and are passed through completely unchanged. This is the same
`model_copy(update=...)` pattern already used elsewhere in this codebase
(e.g. StateStore.clear_hazard) -- a normal, safe way to call a pure
function with a specific input, not a modification of any shared state.

--- weather_factor input: an explicit, documented proxy ---

assess_segment_risk()'s `weather_factor` is designed as a CURRENT,
single-observation input (Part 10: one real day's rainfall). A
segment-YEAR row has no single "current day" -- using the year's
`max_daily_rainfall_mm` (the single wettest real day recorded that year at
the segment's grid cell) through the REAL, unmodified
`weather_factor.rainfall_mm_to_weather_factor()` conversion is the closest
defensible proxy available: "how severe was this year's worst single-day
rainfall, read through the same real IMD-anchored conversion the live app
already uses." This is NOT the same as knowing the actual triggering day's
rainfall for a year-only-dated event -- see
ml_modeling_feasibility_part14.md's limitations section for this caveat
stated plainly.
"""
import pandas as pd

from app.core.risk_engine import assess_segment_risk
from app.core.weather_factor import rainfall_mm_to_weather_factor
from app.data.network_loader import load_network


def compute_baseline_scores(df: pd.DataFrame) -> pd.Series:
    """
    Returns a pandas Series (aligned to df's index) of the real, unmodified
    production risk_engine risk_score for every segment-year row, computed
    from leakage-safe (prior-cutoff) inputs only.
    """
    _nodes, segments = load_network()
    segments_by_id = {s.id: s for s in segments}

    scores = []
    for row in df.itertuples(index=False):
        base_segment = segments_by_id[row.segment_id]
        leakage_safe_segment = base_segment.model_copy(
            update={
                "historical_landslide_count": int(row.historical_landslide_count_prior),
                "nearest_landslide_distance_m": (
                    None
                    if pd.isna(row.nearest_historical_landslide_distance_m_prior)
                    else float(row.nearest_historical_landslide_distance_m_prior)
                ),
            }
        )
        weather_factor = rainfall_mm_to_weather_factor(row.max_daily_rainfall_mm)
        result = assess_segment_risk(leakage_safe_segment, weather_factor=weather_factor, incident_factor=None)
        scores.append(result.risk_score)

    return pd.Series(scores, index=df.index, name="baseline_risk_score")
