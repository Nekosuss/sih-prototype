"""
Part 15B: an ISOLATED ML inference service producing an advisory
`MLRiskSignal` (app/models/ml_risk.py) from the Part 15A v2 (17-feature,
no rainfall) Random Forest artifact
(backend/app/data/ml/artifacts/v2_17_feature/).

--- What this module is NOT ---

This is inference infrastructure ONLY. Nothing in this module is imported
by, or imports, core/risk_engine.py, core/routing_engine.py,
core/reroute_service.py, or core/hazard_state.py -- exactly like
risk_engine.py has zero routing knowledge today, this module has zero
knowledge of routing, hazards, or rerouting. It is not called from any API
route yet. get_ml_risk_signal() below is a pure, side-effect-free function
(module-level artifact caching aside -- see _load_artifact()) that a
future, separately-approved part may choose to call; calling it today has
no effect on anything else in this application.

--- Master switch: ML_RISK_ENABLED (app/config.py) ---

get_ml_risk_signal() checks `app.config.ML_RISK_ENABLED` FIRST, before
touching any file or model. When False (the default), it returns
immediately with `available=False, reason="disabled"` and never even
attempts to load the artifact -- so leaving this disabled is not just
"the model outputs get ignored," it is "this module does nothing at all."

--- Score semantics: read app/models/ml_risk.py before using this ---

The returned `score` is `RandomForestClassifier.predict_proba()`'s raw
positive-class output. It is bounded in [0,1] by construction, but
boundedness is not calibration -- see
backend/app/data/ml/artifacts/v2_17_feature/MODEL_CARD.md (inherited
verbatim from v1's MODEL_CARD.md: "not a probability of a landslide
occurring," no calibration step was ever run, and no confirmed-negative
label exists in the training data to calibrate against). Call it "ML risk
signal" or "ML ranking score." Never "probability."

--- Feature parity with v2 training (ml_feature_parity_part15a.md) ---

_build_feature_row() below constructs its feature row by calling
app.data.ml.feature_matrix_v2_17feature.build_feature_matrix_v2() --
REUSING the exact training-time encoding (same categorical dummy-coding,
same missing-distance sentinel, same has_prior_history derivation) rather
than reimplementing it by hand here, which would risk silently drifting
from what the artifact was actually trained on. All 17 features are drawn
directly from a real, already-loaded RoadSegment; none is invented,
defaulted, or backfilled with a placeholder -- see the module docstring of
feature_matrix_v2_17feature.py and ml_feature_parity_part15a.md Section 6
for why that rule is load-bearing here, not just a style preference.

--- Fail-safe by construction ---

Every failure path in this module -- missing artifact, corrupt joblib,
manifest/schema mismatch, an unusable feature for this segment/date, a
raised exception during predict_proba(), or a NaN/out-of-range model
output -- resolves to `MLRiskSignal(available=False, reason=...)`.
get_ml_risk_signal() NEVER raises for any of these; the only exceptions
that can propagate out of it are programming errors in a caller's own
arguments (e.g. passing something that isn't a RoadSegment), not anything
about the model or its inputs.
"""
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

import app.config as app_config
from app.data.ml.feature_matrix_v2_17feature import build_feature_matrix_v2
from app.models.ml_risk import MLRiskSignal
from app.models.network import RoadSegment

logger = logging.getLogger(__name__)


class _FeatureUnavailable(Exception):
    """Raised internally when a segment/date cannot honestly be turned
    into a valid 17-feature row. Never escapes get_ml_risk_signal()."""


@dataclass(frozen=True)
class _LoadedArtifact:
    model: object  # a fitted sklearn RandomForestClassifier
    experiment_id: str
    feature_names: list[str]
    feature_schema_fingerprint: str


def _fingerprint(feature_names: list[str]) -> str:
    """Short, stable identifier for a feature schema -- changes if and
    only if the ordered column list changes. Used both to report
    `feature_schema_version` and to detect a schema mismatch at load
    time (see _load_artifact_uncached)."""
    digest = hashlib.sha256("|".join(feature_names).encode("utf-8")).hexdigest()
    return digest[:16]


def _load_artifact_uncached(artifact_dir: Path) -> _LoadedArtifact:
    """Raises on ANY problem -- caller (_load_artifact) is the one that
    catches and converts to an 'unavailable' outcome. Kept as a separate,
    exception-raising function (rather than swallowing errors here) so
    each specific failure has one precise place it's detected, and so
    tests can exercise this function directly if they want the raw
    exception rather than the wrapped None."""
    manifest_path = artifact_dir / "model_manifest.json"
    schema_path = artifact_dir / "feature_schema.json"
    model_path = artifact_dir / "random_forest_model.joblib"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    experiment_id = manifest["experiment_id"]
    if experiment_id != app_config.ML_EXPECTED_EXPERIMENT_ID:
        raise ValueError(
            f"Artifact experiment_id {experiment_id!r} does not match expected "
            f"{app_config.ML_EXPECTED_EXPERIMENT_ID!r} -- refusing to load an "
            "incompatible/unexpected artifact."
        )

    feature_names = schema["feature_names_in_order"]
    if not isinstance(feature_names, list) or len(feature_names) != 17:
        raise ValueError(
            f"Artifact feature_schema.json does not describe a 17-feature schema "
            f"(got {len(feature_names) if isinstance(feature_names, list) else 'non-list'})."
        )

    model = joblib.load(model_path)
    if not hasattr(model, "predict_proba"):
        raise TypeError(f"Loaded artifact at {model_path} has no predict_proba() -- not a classifier.")

    # Extra check beyond the JSON schema file: if the fitted estimator
    # itself recorded the feature names it was fit with (sklearn does this
    # when .fit() is called with a DataFrame, which save_model_artifacts_
    # v2_17feature.py does), it must agree with feature_schema.json exactly
    # -- catches the artifact directory being left in a half-updated state
    # (e.g. a swapped .joblib file with a stale feature_schema.json).
    model_feature_names = getattr(model, "feature_names_in_", None)
    if model_feature_names is not None and list(model_feature_names) != feature_names:
        raise ValueError(
            "Model's own feature_names_in_ does not match feature_schema.json's "
            "feature_names_in_order -- artifact directory is internally inconsistent."
        )

    return _LoadedArtifact(
        model=model,
        experiment_id=experiment_id,
        feature_names=feature_names,
        feature_schema_fingerprint=_fingerprint(feature_names),
    )


_artifact_cache: dict[str, Optional[_LoadedArtifact]] = {}


def _load_artifact(artifact_dir: Optional[Path] = None) -> Optional[_LoadedArtifact]:
    """Loads once per distinct artifact_dir and caches the result
    (including a cached `None` for "known unavailable," so a broken
    artifact doesn't get re-attempted on every request). Reads
    app.config.ML_ARTIFACT_DIR fresh on each call (via the `app_config`
    module reference, not a copied import) so tests can monkeypatch it."""
    resolved_dir = Path(artifact_dir) if artifact_dir is not None else Path(app_config.ML_ARTIFACT_DIR)
    key = str(resolved_dir)
    if key in _artifact_cache:
        return _artifact_cache[key]

    try:
        artifact = _load_artifact_uncached(resolved_dir)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see module docstring
        logger.warning("ML risk signal artifact unavailable at %s: %s", resolved_dir, exc)
        _artifact_cache[key] = None
        return None

    _artifact_cache[key] = artifact
    return artifact


def clear_artifact_cache() -> None:
    """Test-only reset -- production code never needs to call this (the
    artifact directory does not change during a process's lifetime)."""
    _artifact_cache.clear()


def _build_feature_row(segment: RoadSegment, as_of_date: date, expected_feature_names: list[str]) -> pd.DataFrame:
    """
    Builds the exact 17-column row build_feature_matrix_v2() would produce
    for this segment, reindexed to `expected_feature_names`. Raises
    _FeatureUnavailable (never returns a partially-invented row) if any
    required value is missing or unsupported.

    Every value here comes directly off the real, already-loaded
    `segment` -- see the module docstring's "Feature parity" section for
    why this delegates the actual encoding to feature_matrix_v2_17feature
    rather than reimplementing it.
    """
    if segment.slope_deg is None:
        raise _FeatureUnavailable(f"segment {segment.id!r}: slope_deg unavailable (no DEM coverage)")
    if segment.elevation_m is None:
        raise _FeatureUnavailable(f"segment {segment.id!r}: elevation_m unavailable (no DEM coverage)")

    road_type_categories = {
        name[len("road_type_"):] for name in expected_feature_names if name.startswith("road_type_")
    }
    terrain_type_categories = {
        name[len("terrain_type_"):] for name in expected_feature_names if name.startswith("terrain_type_")
    }
    if segment.road_type.value not in road_type_categories:
        raise _FeatureUnavailable(
            f"segment {segment.id!r}: road_type {segment.road_type.value!r} is not one of the "
            f"categories this artifact was trained on ({sorted(road_type_categories)})"
        )
    if segment.terrain_type.value not in terrain_type_categories:
        raise _FeatureUnavailable(
            f"segment {segment.id!r}: terrain_type {segment.terrain_type.value!r} is not one of the "
            f"categories this artifact was trained on ({sorted(terrain_type_categories)})"
        )

    nearest_distance = segment.nearest_landslide_distance_m
    row = {
        # segment_id/year/label_status are required by build_feature_matrix_v2()'s
        # input shape (it mirrors the training CSV's columns) but do not
        # themselves become model features -- label_status="unobserved" is an
        # inert placeholder here (it only ever feeds fm.y_pseudo/fm.is_event,
        # both discarded below; it can never make this row look like a
        # trained-on positive, since is_event is never consulted for inference).
        "segment_id": segment.id,
        "year": as_of_date.year,
        "label_status": "unobserved",
        "distance_km": segment.distance_km,
        "slope_deg": segment.slope_deg,
        "elevation_m": segment.elevation_m,
        "historical_landslide_count_prior": segment.historical_landslide_count,
        "nearest_historical_landslide_distance_m_prior": (
            float(nearest_distance) if nearest_distance is not None else np.nan
        ),
        "road_type": segment.road_type.value,
        "terrain_type": segment.terrain_type.value,
    }
    df = pd.DataFrame([row])
    fm = build_feature_matrix_v2(df)
    X = fm.X.reindex(columns=expected_feature_names, fill_value=0)

    if X.isna().any().any():
        raise _FeatureUnavailable(f"segment {segment.id!r}: constructed feature row contains NaN")

    return X


def get_ml_risk_signal(segment: RoadSegment, as_of_date: Optional[date] = None) -> MLRiskSignal:
    """
    The single public entry point. Returns an MLRiskSignal for `segment`
    as of `as_of_date` (defaults to today), or an explicit
    `available=False` with a plain-language `reason` for any failure --
    see module docstring. Never raises for a disabled/unavailable/invalid
    scenario; never mutates `segment`; has no side effects other than the
    module-level artifact cache.
    """
    if not app_config.ML_RISK_ENABLED:
        return MLRiskSignal(available=False, reason="ML risk signal disabled by configuration (ML_RISK_ENABLED=False)")

    artifact = _load_artifact()
    if artifact is None:
        return MLRiskSignal(available=False, reason="ML model artifact unavailable or invalid (see server logs)")

    resolved_date = as_of_date if as_of_date is not None else date.today()
    if resolved_date.year <= app_config.ML_HISTORICAL_PROXY_VALID_FROM_YEAR - 1:
        return MLRiskSignal(
            available=False,
            model_version=artifact.experiment_id,
            feature_schema_version=artifact.feature_schema_fingerprint,
            reason=(
                f"as_of_date year {resolved_date.year} is not after "
                f"{app_config.ML_HISTORICAL_PROXY_VALID_FROM_YEAR - 1} -- the lifetime "
                "historical-landslide proxy (see ml_feature_parity_part15a.md #4/#5) is not "
                "asserted valid for this date"
            ),
        )

    try:
        row = _build_feature_row(segment, resolved_date, artifact.feature_names)
    except _FeatureUnavailable as exc:
        return MLRiskSignal(
            available=False,
            model_version=artifact.experiment_id,
            feature_schema_version=artifact.feature_schema_fingerprint,
            reason=str(exc),
        )

    try:
        proba = float(artifact.model.predict_proba(row)[:, 1][0])
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see module docstring
        logger.warning("ML risk signal inference exception for segment %r: %s", segment.id, exc)
        return MLRiskSignal(
            available=False,
            model_version=artifact.experiment_id,
            feature_schema_version=artifact.feature_schema_fingerprint,
            reason="model inference raised an exception (see server logs)",
        )

    if not np.isfinite(proba) or not (0.0 <= proba <= 1.0):
        return MLRiskSignal(
            available=False,
            model_version=artifact.experiment_id,
            feature_schema_version=artifact.feature_schema_fingerprint,
            reason=f"model returned a non-finite or out-of-range score ({proba!r})",
        )

    return MLRiskSignal(
        available=True,
        score=round(proba, 4),
        model_version=artifact.experiment_id,
        feature_schema_version=artifact.feature_schema_fingerprint,
        reason="ok",
    )
