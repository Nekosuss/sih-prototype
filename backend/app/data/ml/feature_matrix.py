"""
Part 14.3: turns backend/app/data/derived/segment_year_dataset.csv into a
numeric feature matrix for the feasibility study in this same package.
Read-only with respect to the dataset; introduces no new leakage beyond
what build_segment_year_dataset.py already guards against (its prior-cutoff
columns are used as-is, never recomputed here).

--- Grouping key: OSM way, not segment_id (leakage check #7) ---

A single physical road (one OSM `way`) is frequently split into multiple
RoadSegment rows (`seg_<way_id>_<index>`) by the network loader. Checked
directly against the 29 distinct positive segment_ids: they collapse to
only **25 distinct way-id groups** -- three way-ids contribute 2-3
"different" positive segments each (e.g. `seg_238496657_1/_2/_4` are three
pieces of the same physical stretch, all matched to nearby 2021 GSI
records). Grouping cross-validation by raw segment_id would let two
near-identical pieces of the same hillside end up on opposite sides of a
train/validation split -- not a genuine test of generalization. Every
grouped evaluation in this package groups by `way_id`, not `segment_id`.

--- Pseudo-negative training label (explicit, not hidden) ---

`y_train_pseudo` treats every `unobserved` row as 0 for MODEL FITTING only.
This is the standard (if biased) "treat unlabeled as negative" PU-learning
simplification -- NOT a claim that these rows are confirmed safe. It is
used only to give scikit-learn something to fit; every reported evaluation
metric in this study is a RANKING metric computed against the real
`event`/`unobserved` distinction (see ranking_evaluation.py), never
accuracy/precision/recall/F1 against `y_train_pseudo` as if it were ground
truth.
"""
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

# A large, explicit sentinel for "no prior-dated historical match exists"
# -- NaN is not fed to sklearn models directly; this + the companion
# boolean flag below preserves "unknown/none yet" as a real signal rather
# than imputing a misleading small distance (e.g. 0).
NO_PRIOR_HISTORY_DISTANCE_SENTINEL_M = 5000.0

CATEGORICAL_COLUMNS = ["road_type", "terrain_type"]
NUMERIC_FEATURE_COLUMNS = [
    "distance_km",
    "slope_deg",
    "elevation_m",
    "historical_landslide_count_prior",
    "nearest_historical_landslide_distance_m_prior",
    "has_prior_history",
    "annual_rainfall_mm",
    "monsoon_jun_sep_rainfall_mm",
    "max_daily_rainfall_mm",
    "rainy_days_count",
]


@dataclass
class FeatureMatrix:
    X: pd.DataFrame  # numeric feature matrix, one row per segment-year
    feature_names: list[str]
    y_pseudo: np.ndarray  # 1 for event, 0 for unobserved/non_event_documented -- see module docstring
    is_event: np.ndarray  # boolean, real event rows only (never a pseudo-label)
    segment_id: np.ndarray
    way_id: np.ndarray
    year: np.ndarray
    label_status: np.ndarray
    terrain_type: np.ndarray


def _way_id(segment_id: str) -> str:
    m = re.match(r"seg_(\d+)_\d+$", segment_id)
    return m.group(1) if m else segment_id


def load_dataset(csv_path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    expected = 2964 * 11
    if len(df) != expected:
        raise ValueError(f"Expected {expected} rows (2,964 segments x 11 years), got {len(df)}")
    return df


def build_feature_matrix(df: pd.DataFrame) -> FeatureMatrix:
    df = df.copy()
    df["has_prior_history"] = (df["historical_landslide_count_prior"] > 0).astype(int)
    df["nearest_historical_landslide_distance_m_prior"] = df[
        "nearest_historical_landslide_distance_m_prior"
    ].fillna(NO_PRIOR_HISTORY_DISTANCE_SENTINEL_M)

    encoded = pd.get_dummies(df[CATEGORICAL_COLUMNS], prefix=CATEGORICAL_COLUMNS)
    X = pd.concat([df[NUMERIC_FEATURE_COLUMNS], encoded], axis=1)
    feature_names = list(X.columns)

    is_event = (df["label_status"] == "event").to_numpy()
    y_pseudo = is_event.astype(int)

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
