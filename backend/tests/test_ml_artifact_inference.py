"""
Part 14.4: confirms the saved model artifacts (backend/app/data/ml/artifacts/)
are loadable and usable WITHOUT retraining, and that reloading them from
disk reproduces identical scores to the in-memory models that produced
them. This is deliberately NOT connected to the production risk engine --
it only exercises the standalone artifact files.
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pytest

from app.data.ml.feature_matrix import build_feature_matrix, load_dataset
from app.data.ml.models import make_random_forest

ARTIFACTS_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "ml" / "artifacts"
DATASET_CSV = Path(__file__).resolve().parents[1] / "app" / "data" / "derived" / "segment_year_dataset.csv"

pytestmark = pytest.mark.skipif(
    not (ARTIFACTS_DIR / "random_forest_model.joblib").exists(),
    reason="Artifacts not built yet -- run python -m app.data.ml.save_model_artifacts first",
)


@pytest.fixture(scope="module")
def df():
    return load_dataset(DATASET_CSV)


@pytest.fixture(scope="module")
def fm(df):
    return build_feature_matrix(df)


@pytest.fixture(scope="module")
def feature_schema():
    return json.loads((ARTIFACTS_DIR / "feature_schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def loaded_rf():
    return joblib.load(ARTIFACTS_DIR / "random_forest_model.joblib")


@pytest.fixture(scope="module")
def loaded_lr():
    return joblib.load(ARTIFACTS_DIR / "logistic_regression_model.joblib")


@pytest.fixture(scope="module")
def loaded_scaler():
    return joblib.load(ARTIFACTS_DIR / "logistic_regression_scaler.joblib")


def test_feature_schema_matches_current_feature_matrix(fm, feature_schema):
    """The persisted schema is the CONTRACT any future inference row must
    match -- confirm it still matches what feature_matrix.py produces
    today (this would only drift if someone changed feature_matrix.py
    without regenerating the artifacts)."""
    assert feature_schema["feature_names_in_order"] == fm.feature_names
    assert feature_schema["n_features"] == len(fm.feature_names)


def test_random_forest_artifact_loads_and_scores_a_real_row(df, fm, loaded_rf, feature_schema):
    """Step: build a valid feature vector from an EXISTING segment-year
    row (a real documented event row, for a meaningful example), reindex
    it to the saved schema's column order, and confirm the loaded model
    (no .fit() call anywhere in this test) produces a valid score."""
    event_row_positions = np.where(fm.is_event)[0]
    assert len(event_row_positions) > 0
    row_position = event_row_positions[0]

    feature_vector = fm.X.iloc[[row_position]].reindex(columns=feature_schema["feature_names_in_order"])
    assert not feature_vector.isna().any().any()

    score = loaded_rf.predict_proba(feature_vector)[0, 1]
    assert 0.0 <= score <= 1.0

    segment_id = fm.segment_id[row_position]
    year = fm.year[row_position]
    print(f"Loaded Random Forest scored a real event row ({segment_id}, {year}) at {score:.4f}")


def test_logistic_regression_artifact_loads_and_scores_a_real_row(fm, loaded_lr, feature_schema):
    row_position = np.where(fm.is_event)[0][0]
    feature_vector = fm.X.iloc[[row_position]].reindex(columns=feature_schema["feature_names_in_order"])
    score = loaded_lr.predict_proba(feature_vector)[0, 1]
    assert 0.0 <= score <= 1.0


def test_standalone_scaler_matches_the_one_embedded_in_the_pipeline(loaded_lr, loaded_scaler):
    embedded_scaler = loaded_lr.named_steps["scale"]
    assert np.allclose(embedded_scaler.mean_, loaded_scaler.mean_)
    assert np.allclose(embedded_scaler.scale_, loaded_scaler.scale_)


def test_random_forest_artifact_scores_every_row_without_error(fm, loaded_rf, feature_schema):
    """Confirms the artifact works over the full real dataset, not just
    one cherry-picked row -- still no retraining involved."""
    X = fm.X.reindex(columns=feature_schema["feature_names_in_order"])
    scores = loaded_rf.predict_proba(X)[:, 1]
    assert len(scores) == len(fm.X)
    assert np.all((scores >= 0.0) & (scores <= 1.0))


# ---------------------------------------------------------------------------
# Reproducibility check (Step 8): does reloading from disk reproduce the
# SAME scores as a freshly-fit model with the identical config/data? This
# checks serialization fidelity + configuration determinism -- NOT the LOGO
# metrics (see MODEL_CARD.md for why the saved model is a different object
# from the 25 transient LOGO fold models that produced the reported 72.9/
# 78.6 percentiles).
# ---------------------------------------------------------------------------


def test_reloaded_random_forest_matches_a_fresh_fit_on_the_same_data(fm, loaded_rf):
    fresh_rf = make_random_forest()
    fresh_rf.fit(fm.X, fm.y_pseudo)

    loaded_scores = loaded_rf.predict_proba(fm.X)[:, 1]
    fresh_scores = fresh_rf.predict_proba(fm.X)[:, 1]

    assert np.allclose(loaded_scores, fresh_scores, atol=1e-9), (
        "A model reloaded from disk must reproduce a freshly-fit model with the same "
        "config/random_state/data within floating-point tolerance."
    )


def test_manifest_and_metadata_files_are_valid_json_with_expected_keys():
    manifest = json.loads((ARTIFACTS_DIR / "model_manifest.json").read_text(encoding="utf-8"))
    for key in [
        "experiment_id", "models", "training_dataset", "feature_names_in_order",
        "validation_strategy", "reported_metrics", "known_limitations",
        "conclusion_from_part_14_3", "prototype_disclaimer",
    ]:
        assert key in manifest, f"model_manifest.json missing expected key: {key}"

    assert "NOT" in manifest["prototype_disclaimer"]
    assert manifest["conclusion_from_part_14_3"] == "ML PROTOTYPE POSSIBLE BUT NOT RELIABLE FOR PRODUCTION"

    validation_metadata = json.loads((ARTIFACTS_DIR / "validation_metadata.json").read_text(encoding="utf-8"))
    assert validation_metadata["random_forest"]["n_folds"] > 0
    assert validation_metadata["logistic_regression"]["n_folds"] > 0


def test_model_card_states_prototype_status_and_key_limitations():
    text = (ARTIFACTS_DIR / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert "NOT RELIABLE FOR PRODUCTION" in text
    assert "calibrated" in text.lower() and "probabilit" in text.lower()
    assert "25 independent positive" in text
    assert "unobserved" in text.lower() and "safe" in text.lower()
