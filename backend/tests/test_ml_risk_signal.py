"""
Part 15B: tests for the isolated ML inference service
(core/ml_risk_signal.py) and its MLRiskSignal output shape
(models/ml_risk.py).

These tests exercise the REAL v2 (17-feature) artifact at
backend/app/data/ml/artifacts/v2_17_feature/ against REAL network segments
(app.data.network_loader.load_network()) for the happy-path/determinism
tests -- the model is never mocked away for those (per Part 15B's explicit
instruction). Failure-mode tests use `tmp_path` copies of the real
artifact directory, deliberately corrupted/altered, plus monkeypatching of
`app.config.ML_ARTIFACT_DIR`/`ML_RISK_ENABLED`/`ML_EXPECTED_EXPERIMENT_ID`
-- never touching the real artifact files on disk.

None of these tests touch core/risk_engine.py, core/routing_engine.py,
core/reroute_service.py, or core/hazard_state.py -- see
test_ml_risk_signal_does_not_affect_existing_risk_score below for the one
test that explicitly checks the isolation boundary between this new
module and the existing, unmodified risk engine.
"""
import datetime
import json
import shutil
from pathlib import Path

import pytest

import app.config as app_config
from app.core import ml_risk_signal as mrs
from app.core.risk_engine import assess_segment_risk
from app.data.network_loader import load_network
from app.models.network import GeoPoint, RoadSegment, RoadType, TerrainType

REAL_ARTIFACT_DIR = Path(app_config.ML_ARTIFACT_DIR)
AS_OF_DATE = datetime.date(2026, 9, 5)  # after ML_HISTORICAL_PROXY_VALID_FROM_YEAR - 1 (2025)


def make_segment(
    slope_deg=12.0,
    elevation_m=800.0,
    historical_landslide_count=2,
    nearest_landslide_distance_m=150.0,
    terrain_type=TerrainType.mountain,
    road_type=RoadType.tertiary,
    distance_km=3.0,
) -> RoadSegment:
    return RoadSegment(
        id="seg_test_ml",
        from_node_id="a",
        to_node_id="b",
        road_type=road_type,
        distance_km=distance_km,
        estimated_travel_time_min=5.0,
        geometry=[GeoPoint(lat=27.0, lng=92.0), GeoPoint(lat=27.01, lng=92.0)],
        terrain_type=terrain_type,
        slope_deg=slope_deg,
        elevation_m=elevation_m,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
        historical_landslide_count=historical_landslide_count,
        nearest_landslide_distance_m=nearest_landslide_distance_m,
    )


@pytest.fixture(autouse=True)
def _reset_ml_state(monkeypatch):
    """Every test gets ML enabled and pointed at the real artifact by
    default, plus a clean cache -- individual tests override what they
    need to. Restores nothing manually beyond monkeypatch's own automatic
    revert, and always clears the module-level cache on the way out so no
    test's artifact_dir override leaks into the next test."""
    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", True)
    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", REAL_ARTIFACT_DIR)
    mrs.clear_artifact_cache()
    yield
    mrs.clear_artifact_cache()


@pytest.fixture(scope="module")
def real_segments():
    _nodes, segments = load_network()
    return segments


# --- 1. Valid v2 artifact loads -------------------------------------------------

def test_valid_v2_artifact_loads():
    artifact = mrs._load_artifact()
    assert artifact is not None
    assert artifact.experiment_id == "part15a_segment_year_v2_17feature"
    assert len(artifact.feature_names) == 17
    assert artifact.feature_names == [
        "distance_km", "slope_deg", "elevation_m",
        "historical_landslide_count_prior", "nearest_historical_landslide_distance_m_prior",
        "has_prior_history",
        "road_type_primary", "road_type_primary_link", "road_type_secondary",
        "road_type_secondary_link", "road_type_tertiary", "road_type_tertiary_link",
        "road_type_trunk", "road_type_trunk_link",
        "terrain_type_hill", "terrain_type_mountain", "terrain_type_plain",
    ]
    assert hasattr(artifact.model, "predict_proba")


# --- 2. Correct 17-feature vector is generated ----------------------------------

def test_feature_row_matches_expected_schema_and_values():
    artifact = mrs._load_artifact()
    segment = make_segment(
        slope_deg=15.5, elevation_m=1200.0, historical_landslide_count=3,
        nearest_landslide_distance_m=200.0, terrain_type=TerrainType.mountain,
        road_type=RoadType.trunk, distance_km=2.5,
    )
    row = mrs._build_feature_row(segment, AS_OF_DATE, artifact.feature_names)

    assert list(row.columns) == artifact.feature_names
    assert row.shape == (1, 17)
    assert row.loc[0, "distance_km"] == 2.5
    assert row.loc[0, "slope_deg"] == 15.5
    assert row.loc[0, "elevation_m"] == 1200.0
    assert row.loc[0, "historical_landslide_count_prior"] == 3
    assert row.loc[0, "nearest_historical_landslide_distance_m_prior"] == 200.0
    assert row.loc[0, "has_prior_history"] == 1
    # one-hot: exactly one road_type_* column and one terrain_type_* column is 1
    road_type_cols = [c for c in row.columns if c.startswith("road_type_")]
    terrain_type_cols = [c for c in row.columns if c.startswith("terrain_type_")]
    assert row.loc[0, road_type_cols].sum() == 1
    assert row.loc[0, "road_type_trunk"] == 1
    assert row.loc[0, terrain_type_cols].sum() == 1
    assert row.loc[0, "terrain_type_mountain"] == 1
    assert not row.isna().any().any()


def test_feature_row_no_prior_history_uses_sentinel_and_zero_flag():
    artifact = mrs._load_artifact()
    segment = make_segment(historical_landslide_count=0, nearest_landslide_distance_m=None)
    row = mrs._build_feature_row(segment, AS_OF_DATE, artifact.feature_names)
    assert row.loc[0, "has_prior_history"] == 0
    assert row.loc[0, "historical_landslide_count_prior"] == 0
    # sentinel from feature_matrix.NO_PRIOR_HISTORY_DISTANCE_SENTINEL_M
    assert row.loc[0, "nearest_historical_landslide_distance_m_prior"] == 5000.0


# --- 3. Deterministic inference, on REAL project data ---------------------------

def test_inference_is_deterministic_on_real_segment(real_segments):
    segment = next(s for s in real_segments if s.terrain_type == TerrainType.mountain and s.slope_deg is not None)
    first = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    second = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)

    assert first.available is True
    assert second.available is True
    assert first.score == second.score
    assert first.model_version == second.model_version == "part15a_segment_year_v2_17feature"
    assert first.feature_schema_version == second.feature_schema_version


def test_happy_path_real_segment_real_model_produces_valid_signal(real_segments):
    """The primary happy-path test -- NOT mocked. Runs the real saved v2
    Random Forest against a real corridor segment."""
    segment = next(s for s in real_segments if s.historical_landslide_count > 0 and s.slope_deg is not None)
    signal = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)

    assert signal.available is True
    assert signal.reason == "ok"
    assert 0.0 <= signal.score <= 1.0
    assert signal.model_version == "part15a_segment_year_v2_17feature"
    assert signal.feature_schema_version is not None
    assert "not" in signal.methodology_note.lower() and "probability" in signal.methodology_note.lower()


# --- 4. Missing artifact -> unavailable -----------------------------------------

def test_missing_artifact_directory_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", tmp_path / "does_not_exist")
    mrs.clear_artifact_cache()
    segment = make_segment()
    signal = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    assert signal.available is False
    assert signal.score is None
    assert "unavailable" in signal.reason.lower()


# --- 5. Corrupt artifact -> unavailable -----------------------------------------

def test_corrupt_model_file_is_unavailable(monkeypatch, tmp_path):
    dest = tmp_path / "corrupt_artifact"
    shutil.copytree(REAL_ARTIFACT_DIR, dest)
    (dest / "random_forest_model.joblib").write_bytes(b"not a real joblib file at all")

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", dest)
    mrs.clear_artifact_cache()
    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False
    assert "unavailable" in signal.reason.lower()


def test_invalid_manifest_json_is_unavailable(monkeypatch, tmp_path):
    dest = tmp_path / "bad_manifest"
    shutil.copytree(REAL_ARTIFACT_DIR, dest)
    (dest / "model_manifest.json").write_text("{ not valid json", encoding="utf-8")

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", dest)
    mrs.clear_artifact_cache()
    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False


def test_incompatible_experiment_id_is_unavailable(monkeypatch, tmp_path):
    dest = tmp_path / "wrong_experiment"
    shutil.copytree(REAL_ARTIFACT_DIR, dest)
    manifest = json.loads((dest / "model_manifest.json").read_text(encoding="utf-8"))
    manifest["experiment_id"] = "some_other_incompatible_version"
    (dest / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", dest)
    mrs.clear_artifact_cache()
    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False


# --- 6. Schema mismatch -> unavailable ------------------------------------------

def test_feature_schema_mismatch_is_unavailable(monkeypatch, tmp_path):
    dest = tmp_path / "bad_schema"
    shutil.copytree(REAL_ARTIFACT_DIR, dest)
    schema = json.loads((dest / "feature_schema.json").read_text(encoding="utf-8"))
    schema["feature_names_in_order"] = schema["feature_names_in_order"][:-1]  # drop one column
    schema["n_features"] = len(schema["feature_names_in_order"])
    (dest / "feature_schema.json").write_text(json.dumps(schema), encoding="utf-8")

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", dest)
    mrs.clear_artifact_cache()
    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False


def test_feature_names_reordered_is_unavailable(monkeypatch, tmp_path):
    """The model's own feature_names_in_ (recorded at fit time) must agree
    with feature_schema.json -- a schema file edited to reorder columns
    (same set, different order) must still be rejected, not silently
    accepted with misaligned columns."""
    dest = tmp_path / "reordered_schema"
    shutil.copytree(REAL_ARTIFACT_DIR, dest)
    schema = json.loads((dest / "feature_schema.json").read_text(encoding="utf-8"))
    names = schema["feature_names_in_order"]
    schema["feature_names_in_order"] = [names[1], names[0]] + names[2:]
    (dest / "feature_schema.json").write_text(json.dumps(schema), encoding="utf-8")

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", dest)
    mrs.clear_artifact_cache()
    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False


# --- 7. Invalid feature values -> unavailable -----------------------------------

def test_missing_slope_deg_is_unavailable():
    segment = make_segment(slope_deg=None)
    signal = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    assert signal.available is False
    assert "slope_deg" in signal.reason


def test_missing_elevation_m_is_unavailable():
    segment = make_segment(elevation_m=None)
    signal = mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    assert signal.available is False
    assert "elevation_m" in signal.reason


def test_as_of_date_before_proxy_validity_window_is_unavailable():
    segment = make_segment()
    signal = mrs.get_ml_risk_signal(segment, as_of_date=datetime.date(2021, 1, 1))
    assert signal.available is False
    assert "as_of_date" in signal.reason


# --- 8. ML disabled -> no effect on production behavior -------------------------

def test_ml_disabled_returns_unavailable_without_loading_artifact(monkeypatch):
    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", False)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("artifact loader must not be called when ML_RISK_ENABLED is False")

    monkeypatch.setattr(mrs, "_load_artifact", _fail_if_called)

    signal = mrs.get_ml_risk_signal(make_segment(), as_of_date=AS_OF_DATE)
    assert signal.available is False
    assert "disabled" in signal.reason.lower()


def test_ml_disabled_does_not_affect_existing_risk_score(real_segments, monkeypatch):
    """The isolation guarantee: calling get_ml_risk_signal (enabled or
    disabled) must never change what assess_segment_risk() returns for
    the same segment -- the two modules are not wired together."""
    segment = next(s for s in real_segments if s.slope_deg is not None)

    before = assess_segment_risk(segment)

    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", False)
    mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    after_disabled = assess_segment_risk(segment)

    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", True)
    mrs.clear_artifact_cache()
    mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)
    after_enabled = assess_segment_risk(segment)

    assert before == after_disabled == after_enabled


def test_ml_risk_signal_never_raises_for_any_failure_mode(monkeypatch, tmp_path):
    """Sweeps every documented failure mode and confirms none of them ever
    raises out of get_ml_risk_signal -- the mandatory fail-safe contract."""
    segment = make_segment()

    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", False)
    mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)  # must not raise

    monkeypatch.setattr(app_config, "ML_RISK_ENABLED", True)
    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", tmp_path / "missing")
    mrs.clear_artifact_cache()
    mrs.get_ml_risk_signal(segment, as_of_date=AS_OF_DATE)  # must not raise

    monkeypatch.setattr(app_config, "ML_ARTIFACT_DIR", REAL_ARTIFACT_DIR)
    mrs.clear_artifact_cache()
    mrs.get_ml_risk_signal(make_segment(slope_deg=None), as_of_date=AS_OF_DATE)  # must not raise
    mrs.get_ml_risk_signal(segment, as_of_date=datetime.date(1999, 1, 1))  # must not raise
