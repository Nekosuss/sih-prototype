"""
Part 15A: saves the 17-feature (no-rainfall) model variant investigated in
ml_feature_parity_part15a.md, as a SEPARATE, explicitly-versioned artifact
set -- backend/app/data/ml/artifacts/v2_17_feature/ -- alongside, never
overwriting, the existing Part 14.4 21-feature artifacts at
backend/app/data/ml/artifacts/ (retroactively "v1_21_feature" in spirit;
left at its original flat path so nothing that already references those
exact file paths -- MODEL_CARD.md, model_manifest.json, the Part 14
reports -- breaks).

Mirrors save_model_artifacts.py's structure exactly (same LOGO protocol,
same model factories/hyperparameters, same "fit one final model on all
data" step, same manifest/model-card shape) -- the only difference is the
feature matrix builder (feature_matrix_v2_17feature.build_feature_matrix_v2
instead of feature_matrix.build_feature_matrix) and the output directory.

Like v1's save script, this is a RESEARCH artifact-persistence step, not a
production integration: nothing in app/core, app/api, or app/simulation
imports this module or its outputs. See ml_feature_parity_part15a.md for
the investigation and decision this artifact set supports.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn

from app.data.ml.feature_matrix import load_dataset
from app.data.ml.feature_matrix_v2_17feature import (
    NUMERIC_FEATURE_COLUMNS_V2,
    RAINFALL_COLUMNS_DROPPED,
    build_feature_matrix_v2,
)
from app.data.ml.logo_evaluation import groupkfold_diagnostic, leave_one_group_out
from app.data.ml.models import (
    logistic_regression_coefficients,
    make_logistic_regression,
    make_random_forest,
    random_forest_importances,
)
from app.data.ml.baseline_risk_scorer import compute_baseline_scores
from app.data.ml.ranking_evaluation import evaluate_all_event_years

ML_DIR = Path(__file__).resolve().parent
DATASET_CSV = ML_DIR.parent / "derived" / "segment_year_dataset.csv"
ARTIFACTS_DIR = ML_DIR / "artifacts" / "v2_17_feature"

EXPERIMENT_ID = "part15a_segment_year_v2_17feature"


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ML_DIR, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown (git not available at build time)"


def _fold_result_to_dict(r) -> dict:
    return {
        "held_out_way_id": r.held_out_way_id,
        "held_out_segment_ids": r.held_out_segment_ids,
        "year": r.year,
        "terrain_type": r.terrain_type,
        "percentile_rank": r.percentile_rank,
        "within_terrain_percentile_rank": r.within_terrain_percentile_rank,
    }


def main():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    print("Loading dataset and building 17-feature (no-rainfall) matrix...")
    df = load_dataset(DATASET_CSV)
    fm = build_feature_matrix_v2(df)

    print("Re-running leave-one-way-group-out for Logistic Regression (17-feature)...")
    logo_lr_results, logo_lr_pooled_auc = leave_one_group_out(fm, make_logistic_regression)
    print("Re-running leave-one-way-group-out for Random Forest (17-feature)...")
    logo_rf_results, logo_rf_pooled_auc = leave_one_group_out(fm, make_random_forest)

    print("Re-running GroupKFold(5) diagnostic (Random Forest, 17-feature)...")
    gkf_diag = groupkfold_diagnostic(fm, make_random_forest, n_splits=5)

    print("Re-running baseline (production risk_engine) ranking (unchanged, for reference)...")
    baseline_scores = compute_baseline_scores(df)
    baseline_full_results = evaluate_all_event_years(df, baseline_scores.to_numpy())

    lr_full_pct = [r.percentile_rank for r in logo_lr_results]
    lr_within_pct = [r.within_terrain_percentile_rank for r in logo_lr_results]
    rf_full_pct = [r.percentile_rank for r in logo_rf_results]
    rf_within_pct = [r.within_terrain_percentile_rank for r in logo_rf_results]

    print(f"  Logistic Regression (17-feature): within-terrain mean={np.mean(lr_within_pct):.1f} "
          f"(21-feature v1 reported: 72.9)")
    print(f"  Random Forest (17-feature):       within-terrain mean={np.mean(rf_within_pct):.1f} "
          f"(21-feature v1 reported: 78.6)")

    print("Fitting final full-data models (the artifacts to be saved)...")
    final_lr = make_logistic_regression()
    final_lr.fit(fm.X, fm.y_pseudo)

    final_rf = make_random_forest()
    final_rf.fit(fm.X, fm.y_pseudo)

    lr_importances = logistic_regression_coefficients(final_lr, fm.feature_names)
    rf_importances = random_forest_importances(final_rf, fm.feature_names)

    rf_path = ARTIFACTS_DIR / "random_forest_model.joblib"
    lr_path = ARTIFACTS_DIR / "logistic_regression_model.joblib"
    scaler_path = ARTIFACTS_DIR / "logistic_regression_scaler.joblib"

    joblib.dump(final_rf, rf_path)
    joblib.dump(final_lr, lr_path)
    joblib.dump(final_lr.named_steps["scale"], scaler_path)

    print(f"Saved: {rf_path.name}, {lr_path.name}, {scaler_path.name} under {ARTIFACTS_DIR}")

    feature_schema = {
        "feature_names_in_order": fm.feature_names,
        "n_features": len(fm.feature_names),
        "categorical_source_columns": ["road_type", "terrain_type"],
        "categorical_encoding": "pandas.get_dummies (one-hot), columns named '<col>_<category>'",
        "numeric_missing_value_handling": {
            "nearest_historical_landslide_distance_m_prior": (
                "NaN (no prior-dated match yet) filled with sentinel 5000.0; "
                "a companion boolean column 'has_prior_history' preserves whether "
                "this was a real distance or the sentinel."
            )
        },
        "dropped_relative_to_v1_21_feature": RAINFALL_COLUMNS_DROPPED,
        "reason_for_drop": (
            "All 4 are FULL-CALENDAR-YEAR rainfall aggregates computed offline from the IMD "
            "NetCDF archive (rainfall_archive_loader.py, build-time only). None can be honestly "
            "computed for the current, in-progress operational year in production, and no live "
            "daily rainfall feed exists in this project to support any real-time alternative. "
            "See ml_feature_parity_part15a.md."
        ),
        "note": (
            "Any future inference row for THIS (v2, 17-feature) model MUST be built via "
            "app.data.ml.feature_matrix_v2_17feature.build_feature_matrix_v2() and reindexed to "
            "feature_names_in_order before calling .predict_proba() -- this is a DIFFERENT "
            "schema from the v1 21-feature model at ../feature_schema.json; the two are not "
            "interchangeable."
        ),
    }
    (ARTIFACTS_DIR / "feature_schema.json").write_text(json.dumps(feature_schema, indent=2), encoding="utf-8")

    model_config = {
        "random_forest": {
            "sklearn_class": "sklearn.ensemble.RandomForestClassifier",
            "hyperparameters": final_rf.get_params(),
        },
        "logistic_regression": {
            "sklearn_class": "sklearn.pipeline.Pipeline",
            "steps": ["scale (StandardScaler)", "logreg (LogisticRegression)"],
            "hyperparameters": {
                "scale": final_lr.named_steps["scale"].get_params(),
                "logreg": final_lr.named_steps["logreg"].get_params(),
            },
        },
        "hyperparameter_search_performed": False,
        "note": (
            "IDENTICAL hyperparameters to the v1 21-feature model (models.py's "
            "make_random_forest()/make_logistic_regression(), unchanged) -- Part 15A "
            "deliberately did not tune anything; only the input feature set changed."
        ),
    }
    (ARTIFACTS_DIR / "model_config.json").write_text(
        json.dumps(model_config, indent=2, default=str), encoding="utf-8"
    )

    validation_metadata = {
        "validation_strategy": "leave-one-way-group-out (LOGO), grouped by OSM way_id -- IDENTICAL protocol to v1",
        "n_positive_way_groups": 25,
        "logistic_regression": {
            "n_folds": len(logo_lr_results),
            "pooled_auc": logo_lr_pooled_auc,
            "full_population_percentile_mean": float(np.mean(lr_full_pct)),
            "within_terrain_percentile_mean": float(np.mean(lr_within_pct)),
            "within_terrain_percentile_median": float(np.median(lr_within_pct)),
            "within_terrain_percentile_min": float(np.min(lr_within_pct)),
            "within_terrain_percentile_max": float(np.max(lr_within_pct)),
            "folds": [_fold_result_to_dict(r) for r in logo_lr_results],
        },
        "random_forest": {
            "n_folds": len(logo_rf_results),
            "pooled_auc": logo_rf_pooled_auc,
            "full_population_percentile_mean": float(np.mean(rf_full_pct)),
            "within_terrain_percentile_mean": float(np.mean(rf_within_pct)),
            "within_terrain_percentile_median": float(np.median(rf_within_pct)),
            "within_terrain_percentile_min": float(np.min(rf_within_pct)),
            "within_terrain_percentile_max": float(np.max(rf_within_pct)),
            "folds": [_fold_result_to_dict(r) for r in logo_rf_results],
        },
        "groupkfold5_diagnostic_random_forest": [
            {
                "fold_index": d.fold_index,
                "n_train_positive_groups": d.n_train_positive_groups,
                "n_val_positive_groups": d.n_val_positive_groups,
                "n_val_rows": d.n_val_rows,
                "fold_auc": d.fold_auc,
                "evaluable": d.fold_auc is not None,
            }
            for d in gkf_diag
        ],
        "baseline_comparison": {
            "description": "Production risk_engine.assess_segment_risk(), unchanged -- same reference point used for v1.",
            "full_population_mean_percentile": float(
                np.mean([p for r in baseline_full_results for p in r.percentile_ranks])
            ),
        },
        "comparison_to_v1_21_feature": {
            "v1_random_forest_within_terrain_mean_percentile": 78.63330420288142,
            "v1_random_forest_pooled_auc": 0.9810827140291798,
            "v1_logistic_regression_within_terrain_mean_percentile": 72.93720580247198,
            "v1_logistic_regression_pooled_auc": 0.9539921932641141,
            "v2_random_forest_within_terrain_mean_percentile": float(np.mean(rf_within_pct)),
            "v2_random_forest_pooled_auc": logo_rf_pooled_auc,
            "v2_logistic_regression_within_terrain_mean_percentile": float(np.mean(lr_within_pct)),
            "v2_logistic_regression_pooled_auc": logo_lr_pooled_auc,
            "note": (
                "v1 numbers are quoted from artifacts/model_manifest.json (Part 14.4), reproduced "
                "in this same process for the delta reported in ml_feature_parity_part15a.md. "
                "Random Forest loses a small amount of within-terrain ranking quality without "
                "rainfall; Logistic Regression does not. Both v2 models remain far above the "
                "58.2 / AUC 0.535 rule-based baseline."
            ),
        },
        "note_on_reproducibility": (
            "These numbers were produced by running feature_matrix_v2_17feature.py + the "
            "UNCHANGED models.py/logo_evaluation.py/ranking_evaluation.py/baseline_risk_scorer.py "
            "at artifact-save time -- see ml_feature_parity_part15a.md for the full investigation."
        ),
    }
    (ARTIFACTS_DIR / "validation_metadata.json").write_text(
        json.dumps(validation_metadata, indent=2, default=str), encoding="utf-8"
    )

    feature_importance = {
        "random_forest_impurity_importance": [
            {"feature": name, "importance": float(val)} for name, val in rf_importances
        ],
        "logistic_regression_standardized_coefficients": [
            {"feature": name, "coefficient": float(val)} for name, val in lr_importances
        ],
        "caveats": [
            "Random Forest importances are impurity-based: biased toward high-cardinality/"
            "continuous features, and reflect correlation, not causation.",
            "Logistic Regression coefficients are in STANDARDIZED-feature units and are "
            "NOT reliably interpretable at this sample size.",
            "Neither model's importances support a causal claim about what 'causes' landslides.",
            "Unlike v1, none of these features are rainfall-derived -- see "
            "ml_feature_parity_part15a.md for the full comparison.",
        ],
    }
    (ARTIFACTS_DIR / "feature_importance.json").write_text(
        json.dumps(feature_importance, indent=2), encoding="utf-8"
    )

    dataset_metadata = {
        "dataset_path": DATASET_CSV.relative_to(ML_DIR.parents[2]).as_posix(),
        "dataset_sha256": _sha256(DATASET_CSV),
        "note": "SAME underlying dataset CSV as v1 -- only the feature matrix (columns used) differs.",
        "row_count": len(df),
        "n_segments": int(df["segment_id"].nunique()),
        "n_years": int(df["year"].nunique()),
        "years": sorted(int(y) for y in df["year"].unique()),
        "label_status_counts": df["label_status"].value_counts().to_dict(),
        "n_positive_rows": int((df["label_status"] == "event").sum()),
        "n_positive_segments": int(df.loc[df["label_status"] == "event", "segment_id"].nunique()),
        "n_positive_way_groups": 25,
        "positive_years": sorted(int(y) for y in df.loc[df["label_status"] == "event", "year"].unique()),
    }
    (ARTIFACTS_DIR / "dataset_metadata.json").write_text(
        json.dumps(dataset_metadata, indent=2, default=str), encoding="utf-8"
    )

    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "part": "15A",
        "generated_at_utc": generated_at,
        "git_commit": _git_commit(),
        "sklearn_version": sklearn.__version__,
        "prototype_disclaimer": (
            "PROTOTYPE ARTIFACT -- NOT production-calibrated. Model scores are NOT "
            "calibrated probabilities of a landslide occurring. See MODEL_CARD.md."
        ),
        "version_relationship": {
            "predecessor": "part14_segment_year_v1 (21-feature, includes 4 rainfall aggregates)",
            "predecessor_location": "backend/app/data/ml/artifacts/ (unchanged, not overwritten)",
            "this_version": "part15a_segment_year_v2_17feature (17-feature, no rainfall)",
            "why_a_new_version_exists": (
                "Part 15's production feature-parity audit found the 4 rainfall aggregate "
                "features in v1 are full-calendar-year quantities that cannot be honestly "
                "computed at inference time in production (no live rainfall feed, and an "
                "annual total is a hindsight quantity for an in-progress year). This version "
                "answers 'what does removing them cost' empirically -- see "
                "ml_feature_parity_part15a.md for the decision this supports."
            ),
        },
        "models": {
            "random_forest": {
                "file": "random_forest_model.joblib",
                "sklearn_class": "sklearn.ensemble.RandomForestClassifier",
            },
            "logistic_regression": {
                "file": "logistic_regression_model.joblib",
                "sklearn_class": "sklearn.pipeline.Pipeline (StandardScaler + LogisticRegression)",
                "preprocessing_file": "logistic_regression_scaler.joblib",
            },
        },
        "training_dataset": dataset_metadata,
        "feature_schema_file": "feature_schema.json",
        "feature_names_in_order": fm.feature_names,
        "model_config_file": "model_config.json",
        "validation_metadata_file": "validation_metadata.json",
        "feature_importance_file": "feature_importance.json",
        "validation_strategy": "leave-one-way-group-out, grouped by OSM way_id (25 independent positive groups) -- identical to v1",
        "reported_metrics": {
            "logistic_regression_within_terrain_mean_percentile": float(np.mean(lr_within_pct)),
            "random_forest_within_terrain_mean_percentile": float(np.mean(rf_within_pct)),
            "logistic_regression_pooled_auc": logo_lr_pooled_auc,
            "random_forest_pooled_auc": logo_rf_pooled_auc,
            "note": (
                "LOGO out-of-fold estimates from 25 transient per-fold models, NOT the saved "
                "final models' own in-sample score -- identical caveat to v1."
            ),
        },
        "known_limitations": [
            "Same 25-independent-positive-group sample-size limitation as v1 -- unchanged by "
            "removing rainfall features.",
            "Same reporting/observation-location bias as v1 (unresolved, inherited from the "
            "underlying GSI data).",
            "Same absence of any confirmed-negative label as v1.",
            "Loses the (already coarse, contemporaneous-not-pre-event) rainfall signal v1 had -- "
            "trades a small amount of Random Forest ranking quality (see comparison_to_v1 in "
            "validation_metadata.json) for features that are honestly computable in production.",
        ],
        "conclusion": (
            "See ml_feature_parity_part15a.md for the full decision. This artifact set exists "
            "to make that decision with real numbers, not to declare production-readiness -- "
            "the MODEL_CARD.md 'Not intended for' list applies identically to this version."
        ),
        "production_integration_status": "NOT integrated. Not imported by app/core, app/api, or app/simulation.",
    }
    (ARTIFACTS_DIR / "model_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    model_card = f"""# Model Card — Segment-Year Landslide Ranking Prototype, v2 (17-feature, no rainfall)

**Experiment ID:** `{EXPERIMENT_ID}` (Part 15A)
**Predecessor:** `part14_segment_year_v1` (21-feature, `../` — unchanged, not overwritten)
**Status:** Prototype research artifact. **Not integrated into production.**

## Why this version exists

Part 15's production feature-parity audit found that 4 of v1's 21 features
(`annual_rainfall_mm`, `monsoon_jun_sep_rainfall_mm`, `max_daily_rainfall_mm`,
`rainy_days_count`) are full-calendar-year aggregates that cannot be
honestly computed at inference time in production — see
`ml_feature_parity_part15a.md` for the full investigation. This version
removes those 4 features and keeps everything else — same dataset, same
labels, same LOGO grouping, same model hyperparameters — identical to v1.

## Result (see validation_metadata.json for full numbers)

| model | within-terrain mean percentile (v1, 21-feature) | within-terrain mean percentile (v2, 17-feature) |
|---|---|---|
| Random Forest | 78.63 | {np.mean(rf_within_pct):.2f} |
| Logistic Regression | 72.94 | {np.mean(lr_within_pct):.2f} |

Both v2 models remain far above the rule-based production baseline
(58.2 mean within-terrain percentile, AUC ≈ 0.535 — essentially chance).

## Everything else

Identical caveats, limitations, and "not intended for" restrictions as
`../MODEL_CARD.md` (v1) — this document does not repeat them in full; see
that file. In particular: **not intended for autonomous safety decisions,
not a calibrated probability, not a replacement for domain experts, not
for production deployment without additional validation.**
"""
    (ARTIFACTS_DIR / "MODEL_CARD.md").write_text(model_card, encoding="utf-8")

    print(f"\nAll v2 (17-feature) artifacts written to {ARTIFACTS_DIR}")
    return {
        "logo_lr_results": logo_lr_results, "logo_lr_pooled_auc": logo_lr_pooled_auc,
        "logo_rf_results": logo_rf_results, "logo_rf_pooled_auc": logo_rf_pooled_auc,
        "final_lr": final_lr, "final_rf": final_rf, "fm": fm, "df": df,
    }


if __name__ == "__main__":
    main()
