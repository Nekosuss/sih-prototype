"""
Part 15A: a SECOND feature-matrix builder, identical to feature_matrix.py's
build_feature_matrix() except that the 4 rainfall aggregate columns
(annual_rainfall_mm, monsoon_jun_sep_rainfall_mm, max_daily_rainfall_mm,
rainy_days_count) are dropped.

--- Why this exists as a separate module rather than a parameter on the
    existing build_feature_matrix() ---

feature_matrix.py's NUMERIC_FEATURE_COLUMNS/build_feature_matrix() are the
Part 14.3/14.4 21-feature contract, already referenced by
feature_schema.json, model_manifest.json, and the saved v1 (21-feature)
artifacts under app/data/ml/artifacts/. Editing that module in place would
silently change what an existing, already-documented artifact's "the exact
code that produced this" pointer refers to. This module is instead a
parallel, explicitly-versioned variant (see
ml_feature_parity_part15a.md for the full investigation this supports) --
v1 (21-feature) and v2 (17-feature, this module) coexist, each with its own
artifacts directory, so either can be reloaded and reproduced without
ambiguity about which code built it.

--- Why these 4 columns, specifically ---

See ml_feature_parity_part15a.md Section 1/2: all four are FULL CALENDAR
YEAR aggregates (annual total, Jun-Sep monsoon-window total, yearly max
daily value, yearly count of days > 1mm) computed offline from the IMD
NetCDF archive (rainfall_archive_loader.py, explicitly build-time only,
never imported by app/core/app/api/app/simulation). None of the four can
be honestly computed for "the current, in-progress operational year" in a
running production system -- an annual total is fundamentally a hindsight
quantity, and no live/current daily rainfall feed exists in this project.
This module does NOT invent a substitute rainfall feature (e.g. a
trailing-N-day window) -- ml_feature_parity_part15a.md Section 3
investigates that separately and concludes it is not adoptable without a
live rainfall feed this project does not have. This module simply
evaluates "what if these 4 are absent," using the same road/terrain/DEM/
GSI-history features the training set already has, honestly, in production
today (Part 15's finding: 17 of 21 features already qualify).

Nothing in this module is imported by app/core, app/api, or
app/simulation. It does not modify feature_matrix.py, its constants, or
its behavior.
"""
import pandas as pd

from app.data.ml.feature_matrix import (
    CATEGORICAL_COLUMNS,
    NO_PRIOR_HISTORY_DISTANCE_SENTINEL_M,
    FeatureMatrix,
)

RAINFALL_COLUMNS_DROPPED = [
    "annual_rainfall_mm",
    "monsoon_jun_sep_rainfall_mm",
    "max_daily_rainfall_mm",
    "rainy_days_count",
]

NUMERIC_FEATURE_COLUMNS_V2 = [
    "distance_km",
    "slope_deg",
    "elevation_m",
    "historical_landslide_count_prior",
    "nearest_historical_landslide_distance_m_prior",
    "has_prior_history",
]


def build_feature_matrix_v2(df: pd.DataFrame) -> FeatureMatrix:
    """Identical to feature_matrix.build_feature_matrix() (same categorical
    encoding, same missing-value sentinel, same has_prior_history
    derivation, same y_pseudo/is_event/grouping fields) except the 4
    rainfall columns are never read from `df` and never appear in the
    output feature matrix."""
    df = df.copy()
    df["has_prior_history"] = (df["historical_landslide_count_prior"] > 0).astype(int)
    df["nearest_historical_landslide_distance_m_prior"] = df[
        "nearest_historical_landslide_distance_m_prior"
    ].fillna(NO_PRIOR_HISTORY_DISTANCE_SENTINEL_M)

    encoded = pd.get_dummies(df[CATEGORICAL_COLUMNS], prefix=CATEGORICAL_COLUMNS)
    X = pd.concat([df[NUMERIC_FEATURE_COLUMNS_V2], encoded], axis=1)
    feature_names = list(X.columns)

    is_event = (df["label_status"] == "event").to_numpy()
    y_pseudo = is_event.astype(int)

    def _way_id(segment_id: str) -> str:
        import re

        m = re.match(r"seg_(\d+)_\d+$", segment_id)
        return m.group(1) if m else segment_id

    return FeatureMatrix(
        X=X,
        feature_names=feature_names,
        y_pseudo=y_pseudo,
        is_event=is_event,
        segment_id=df["segment_id"].to_numpy(),
        way_id=df["segment_id"].apply(_way_id).to_numpy(),
        year=df["year"].to_numpy(),
        label_status=df["label_status"].to_numpy(),
        terrain_type=df["terrain_type"].to_numpy(),
    )
