"""
Part 14.4: reruns the EXACT Part 14.3 experiment (same dataset, same
feature_matrix.py, same models.py factories/hyperparameters, same
logo_evaluation.py validation code, same ranking_evaluation.py metrics --
nothing in this script redefines any of those) and, unlike
run_feasibility_study.py (which only prints results), persists every
artifact needed to reload and reuse the result without retraining.

--- What gets saved, and why each one is a SEPARATE model from the LOGO evaluation ---

The Part 14.3 report's headline numbers (Logistic Regression 72.9 mean
within-terrain percentile, Random Forest 78.6) come from
leave-one-way-group-out: 25 DIFFERENT transient models, each trained with
one positive group excluded, used ONLY to produce an honest out-of-fold
score for that excluded group. None of those 25-per-model transient
models is "the" model to keep -- keeping any single one would arbitrarily
exclude one real positive group from what it learned.

Standard practice (and what this script does) is to separately fit ONE
final model of the SAME type/config/features on ALL available data
(all 25 positive groups included) and persist THAT as the reusable
artifact. This is exactly the "fit on FULL data" step
run_feasibility_study.py already ran for feature-importance reporting
(Part 14.3, Section 7) -- this script does not introduce a new model, it
just saves the one that experiment already produced in memory and threw
away.

This distinction is documented prominently in model_manifest.json and
MODEL_CARD.md: the saved model's own in-sample score is NEVER reported as
"the" accuracy metric -- the LOGO out-of-fold numbers (reproduced fresh by
this same script, see validation_metadata.json) remain the only reported
generalization estimate, exactly as in Part 14.3.

--- Nothing here changes dataset/features/labels/validation/preprocessing/
    model configuration from Part 14.3 ---

This script imports and calls the existing functions from feature_matrix.py,
models.py, logo_evaluation.py, ranking_evaluation.py, baseline_risk_scorer.py
unchanged. It adds serialization (joblib + JSON) and a manifest/model
card -- no new modeling logic.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn

from app.data.ml.feature_matrix import build_feature_matrix, load_dataset
from app.data.ml.logo_evaluation import groupkfold_diagnostic, leave_one_group_out
from app.data.ml.models import (
    logistic_regression_coefficients,
    make_logistic_regression,
    make_random_forest,
    random_forest_importances,
)
from app.data.ml.ranking_evaluation import evaluate_all_event_years
from app.data.ml.baseline_risk_scorer import compute_baseline_scores

ML_DIR = Path(__file__).resolve().parent
DATASET_CSV = ML_DIR.parent / "derived" / "segment_year_dataset.csv"
ARTIFACTS_DIR = ML_DIR / "artifacts"

EXPERIMENT_ID = "part14_segment_year_v1"


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

    print("Loading dataset and building feature matrix (unchanged from Part 14.3)...")
    df = load_dataset(DATASET_CSV)
    fm = build_feature_matrix(df)

    # -----------------------------------------------------------------
    # 1. Reproduce the LOGO validation exactly (same code, same configs)
    #    -- this is what "reproduces the previously reported experiment"
    #    means: rerunning the same deterministic code, not re-deriving
    #    new numbers.
    # -----------------------------------------------------------------
    print("Re-running leave-one-way-group-out for Logistic Regression (reproduces Part 14.3)...")
    logo_lr_results, logo_lr_pooled_auc = leave_one_group_out(fm, make_logistic_regression)
    print("Re-running leave-one-way-group-out for Random Forest (reproduces Part 14.3)...")
    logo_rf_results, logo_rf_pooled_auc = leave_one_group_out(fm, make_random_forest)

    print("Re-running GroupKFold(5) diagnostic (Random Forest, reproduces Part 14.3)...")
    gkf_diag = groupkfold_diagnostic(fm, make_random_forest, n_splits=5)

    print("Re-running baseline (production risk_engine) ranking (reproduces Part 14.3)...")
    baseline_scores = compute_baseline_scores(df)
    baseline_full_results = evaluate_all_event_years(df, baseline_scores.to_numpy())

    lr_full_pct = [r.percentile_rank for r in logo_lr_results]
    lr_within_pct = [r.within_terrain_percentile_rank for r in logo_lr_results]
    rf_full_pct = [r.percentile_rank for r in logo_rf_results]
    rf_within_pct = [r.within_terrain_percentile_rank for r in logo_rf_results]

    print(f"  Logistic Regression: within-terrain mean={np.mean(lr_within_pct):.1f} "
          f"(Part 14.3 reported: 72.9)")
    print(f"  Random Forest:       within-terrain mean={np.mean(rf_within_pct):.1f} "
          f"(Part 14.3 reported: 78.6)")

    # -----------------------------------------------------------------
    # 2. Fit ONE final model of each type on ALL data -- the artifact to
    #    keep (see module docstring for why this is a SEPARATE thing from
    #    the 25 transient LOGO models above).
    # -----------------------------------------------------------------
    print("Fitting final full-data models (the artifacts to be saved)...")
    final_lr = make_logistic_regression()
    final_lr.fit(fm.X, fm.y_pseudo)

    final_rf = make_random_forest()
    final_rf.fit(fm.X, fm.y_pseudo)

    lr_importances = logistic_regression_coefficients(final_lr, fm.feature_names)
    rf_importances = random_forest_importances(final_rf, fm.feature_names)

    # -----------------------------------------------------------------
    # 3. Serialize models + scaler
    # -----------------------------------------------------------------
    rf_path = ARTIFACTS_DIR / "random_forest_model.joblib"
    lr_path = ARTIFACTS_DIR / "logistic_regression_model.joblib"
    scaler_path = ARTIFACTS_DIR / "logistic_regression_scaler.joblib"

    joblib.dump(final_rf, rf_path)
    joblib.dump(final_lr, lr_path)
    # The StandardScaler is already embedded as the first step of the saved
    # Pipeline above (loading logistic_regression_model.joblib is
    # sufficient for correct inference on its own) -- also extracted and
    # saved standalone here purely for inspectability, per the task's
    # explicit "preprocessing object, if any" artifact requirement.
    joblib.dump(final_lr.named_steps["scale"], scaler_path)

    print(f"Saved: {rf_path.name}, {lr_path.name}, {scaler_path.name}")

    # -----------------------------------------------------------------
    # 4. Feature schema (ordered feature list -- the contract any future
    #    inference call must match column-for-column, in this exact order)
    # -----------------------------------------------------------------
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
        "note": (
            "Any future inference row MUST be built via "
            "app.data.ml.feature_matrix.build_feature_matrix() (or the same "
            "pandas.get_dummies category set) and reindexed to "
            "feature_names_in_order before calling .predict_proba() -- a "
            "mismatched column set/order will silently produce wrong scores "
            "with plain sklearn estimators."
        ),
    }
    (ARTIFACTS_DIR / "feature_schema.json").write_text(json.dumps(feature_schema, indent=2), encoding="utf-8")

    # -----------------------------------------------------------------
    # 5. Model configuration / hyperparameters -- read back from the
    #    actual fitted estimators (get_params()), not hand-retyped, so
    #    this can never drift from what was really used.
    # -----------------------------------------------------------------
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
        "note": "No grid/random search was run for either model -- see models.py's module docstring.",
    }
    (ARTIFACTS_DIR / "model_config.json").write_text(
        json.dumps(model_config, indent=2, default=str), encoding="utf-8"
    )

    # -----------------------------------------------------------------
    # 6. Validation metadata -- every LOGO fold + the GroupKFold diagnostic
    # -----------------------------------------------------------------
    validation_metadata = {
        "validation_strategy": "leave-one-way-group-out (LOGO), grouped by OSM way_id",
        "validation_group_definition": (
            "OSM way_id, parsed from segment_id as 'seg_<way_id>_<index>' -- NOT raw "
            "segment_id, because a single physical road is frequently split into "
            "multiple RoadSegment rows (measured: 29 distinct positive segment_ids "
            "collapse to 25 distinct way_id groups)."
        ),
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
            "description": (
                "Production risk_engine.assess_segment_risk(), leakage-safe inputs -- "
                "NOT grouped/LOGO-validated (it is a fixed formula, not fit to data, "
                "so there is nothing to hold out)."
            ),
            "full_population_mean_percentile": float(
                np.mean([p for r in baseline_full_results for p in r.percentile_ranks])
            ),
        },
        "note_on_reproducibility": (
            "These numbers were reproduced by RERUNNING the exact Part 14.3 code "
            "(feature_matrix.py, models.py, logo_evaluation.py) at artifact-save time, "
            "not copied from the prior report. See ml_artifact_report_part14.md for the "
            "diff against the originally reported 72.9 / 78.6 mean within-terrain percentiles."
        ),
    }
    (ARTIFACTS_DIR / "validation_metadata.json").write_text(
        json.dumps(validation_metadata, indent=2, default=str), encoding="utf-8"
    )

    # -----------------------------------------------------------------
    # 7. Feature importance (fit-on-full-data models -- interpretability
    #    only, matches Part 14.3 Section 7 exactly)
    # -----------------------------------------------------------------
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
            "NOT reliably interpretable at this sample size -- see MODEL_CARD.md; the "
            "largest-magnitude coefficient (historical_landslide_count_prior) has a sign "
            "that does not support a causal reading and is a known multicollinearity artifact.",
            "Neither model's importances support a causal claim about what 'causes' landslides.",
        ],
    }
    (ARTIFACTS_DIR / "feature_importance.json").write_text(
        json.dumps(feature_importance, indent=2), encoding="utf-8"
    )

    # -----------------------------------------------------------------
    # 8. Dataset metadata
    # -----------------------------------------------------------------
    dataset_metadata = {
        "dataset_path": DATASET_CSV.relative_to(ML_DIR.parents[2]).as_posix(),
        "dataset_sha256": _sha256(DATASET_CSV),
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

    # -----------------------------------------------------------------
    # 9. Manifest
    # -----------------------------------------------------------------
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "part": "14.4",
        "generated_at_utc": generated_at,
        "git_commit": _git_commit(),
        "sklearn_version": sklearn.__version__,
        "prototype_disclaimer": (
            "PROTOTYPE ARTIFACT -- NOT production-calibrated. Model scores are NOT "
            "calibrated probabilities of a landslide occurring. See MODEL_CARD.md."
        ),
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
        "validation_strategy": "leave-one-way-group-out, grouped by OSM way_id (25 independent positive groups)",
        "reported_metrics": {
            "logistic_regression_within_terrain_mean_percentile": float(np.mean(lr_within_pct)),
            "random_forest_within_terrain_mean_percentile": float(np.mean(rf_within_pct)),
            "logistic_regression_pooled_auc": logo_lr_pooled_auc,
            "random_forest_pooled_auc": logo_rf_pooled_auc,
            "note": (
                "These are LOGO out-of-fold estimates from 25 transient per-fold models "
                "(see logo_evaluation.py), NOT the in-sample score of the SAVED final "
                "models above -- the saved models were fit on all 25 positive groups and "
                "have no honest held-out score of their own. Never report the saved "
                "model's own training-data score as if it were the generalization estimate."
            ),
        },
        "known_limitations": [
            "Only 25 independent positive way-groups -- any validation metric here carries "
            "wide, unquantified uncertainty.",
            "Reporting/observation-location bias is unresolved (Part 14 inspection): events "
            "cluster in the high-mountain section, which may reflect survey effort as much "
            "as genuine hazard.",
            "non_event_documented is empty -- there is no confirmed-negative signal in this "
            "dataset at all; every comparison is against 'unobserved', never 'safe'.",
            "Rainfall features are contemporaneous with the label year, not strictly "
            "pre-event, for 24 of 30 event rows (only 2016 events have a day-precise date).",
            "0.25-degree rainfall grid is far coarser than road-segment spacing.",
            "Logistic Regression coefficients are unstable/multicollinear at this sample size.",
        ],
        "conclusion_from_part_14_3": "ML PROTOTYPE POSSIBLE BUT NOT RELIABLE FOR PRODUCTION",
        "production_integration_status": "NOT integrated. Not imported by app/core, app/api, or app/simulation.",
    }
    (ARTIFACTS_DIR / "model_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    print(f"\nAll artifacts written to {ARTIFACTS_DIR}")
    return {
        "logo_lr_results": logo_lr_results, "logo_lr_pooled_auc": logo_lr_pooled_auc,
        "logo_rf_results": logo_rf_results, "logo_rf_pooled_auc": logo_rf_pooled_auc,
        "final_lr": final_lr, "final_rf": final_rf, "fm": fm, "df": df,
    }


if __name__ == "__main__":
    main()
