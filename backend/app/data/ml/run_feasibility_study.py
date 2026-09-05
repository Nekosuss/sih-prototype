"""
Part 14.3: runs the full feasibility study (dataset inspection, baseline
ranking, grouped/LOGO model evaluation, feature importance, leakage
checks) and prints everything needed to write
ml_modeling_feasibility_part14.md. This script does NOT write the final
report itself (that's composed by hand from this script's real output, to
keep the report's prose rigorous rather than templated) -- it also does
NOT integrate anything into production and does NOT persist a trained
model anywhere the running API could load.

Usage:
    cd backend
    python -m app.data.ml.run_feasibility_study
"""
from pathlib import Path

import numpy as np
import pandas as pd

from app.data.ml.baseline_risk_scorer import compute_baseline_scores
from app.data.ml.feature_matrix import build_feature_matrix, load_dataset
from app.data.ml.leakage_checks import run_all_checks
from app.data.ml.logo_evaluation import groupkfold_diagnostic, leave_one_group_out
from app.data.ml.models import (
    logistic_regression_coefficients,
    make_logistic_regression,
    make_random_forest,
    random_forest_importances,
)
from app.data.ml.ranking_evaluation import evaluate_all_event_years, summarize_results

DATASET_CSV = Path(__file__).resolve().parents[1] / "derived" / "segment_year_dataset.csv"


def section(title: str):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    section("1. DATASET SUMMARY")
    df = load_dataset(DATASET_CSV)
    print(f"Rows: {len(df)}  |  Distinct segments: {df['segment_id'].nunique()}  |  Distinct years: {df['year'].nunique()}")
    print("\ndtypes:\n", df.dtypes)
    print("\nmissing values per column:\n", df.isna().sum()[df.isna().sum() > 0])
    print("\nlabel_status counts:\n", df["label_status"].value_counts())
    print("\nnumeric feature describe():\n", df.describe(include=[np.number]).T)

    events = df[df["label_status"] == "event"]
    print(f"\nEvent rows: {len(events)}  |  distinct positive segments: {events['segment_id'].nunique()}")
    print("Event rows by year:\n", events["year"].value_counts().sort_index())

    numeric_cols = [
        "slope_deg", "elevation_m", "historical_landslide_count_prior",
        "annual_rainfall_mm", "monsoon_jun_sep_rainfall_mm", "max_daily_rainfall_mm",
    ]
    print("\nEvent rows vs full-population mean (descriptive only, n=30 -- not a significance test):")
    for col in numeric_cols:
        print(f"  {col}: event mean={events[col].mean():.2f}  all-rows mean={df[col].mean():.2f}")

    print("\nCorrelation of numeric features with is_event (point-biserial via pandas corr, n=30 positives -- descriptive only):")
    corr_df = df[numeric_cols].copy()
    corr_df["is_event"] = (df["label_status"] == "event").astype(int)
    print(corr_df.corr()["is_event"].drop("is_event").sort_values(ascending=False))

    section("2. FEATURE MATRIX")
    fm = build_feature_matrix(df)
    print("Feature columns:", fm.feature_names)
    print("X shape:", fm.X.shape, " positives:", fm.is_event.sum())

    section("3. BASELINE (existing explainable risk engine, leakage-safe inputs)")
    baseline_scores = compute_baseline_scores(df)
    baseline_results = evaluate_all_event_years(df, baseline_scores.to_numpy())
    print(summarize_results(baseline_results, "Baseline (production risk_engine.assess_segment_risk) -- FULL population"))

    print(
        "\n--- WITHIN-TERRAIN check: does the baseline discriminate WITHIN mountain/hill "
        "segments, or is its skill just 'recognize mountain terrain'? ---"
    )
    terrain_mask = df["terrain_type"].isin(["mountain", "hill"]).to_numpy()
    sub_df = df[terrain_mask].reset_index(drop=True)
    sub_scores = baseline_scores.to_numpy()[terrain_mask]
    print(f"Rows in mountain+hill subset: {len(sub_df)} of {len(df)} "
          f"({100*len(sub_df)/len(df):.1f}% of the corridor)")
    print(f"Event rows in subset: {(sub_df['label_status']=='event').sum()} of "
          f"{(df['label_status']=='event').sum()} total events")
    within_terrain_results = evaluate_all_event_years(sub_df, sub_scores)
    print(summarize_results(within_terrain_results, "Baseline restricted to mountain+hill terrain only"))

    section("4. GroupKFold(5) DIAGNOSTIC (grouped by way_id) -- Random Forest")
    gkf_diag = groupkfold_diagnostic(fm, make_random_forest, n_splits=5)
    for d in gkf_diag:
        auc_str = f"{d.fold_auc:.3f}" if d.fold_auc is not None else "NOT EVALUABLE (0 positive groups in validation)"
        print(f"fold {d.fold_index}: train_positive_groups={d.n_train_positive_groups} "
              f"val_positive_groups={d.n_val_positive_groups} val_rows={d.n_val_rows} fold_auc={auc_str}")

    def report_logo(results, pooled_auc, label):
        print(f"n folds: {len(results)}  pooled_auc={pooled_auc}")
        percentiles = [r.percentile_rank for r in results]
        within = [r.within_terrain_percentile_rank for r in results]
        print(f"FULL-POPULATION percentile ranks: mean={np.mean(percentiles):.1f} median={np.median(percentiles):.1f} "
              f"min={np.min(percentiles):.1f} max={np.max(percentiles):.1f}")
        print(f"  folds in top 10%: {sum(1 for p in percentiles if p >= 90)}/{len(percentiles)}")
        print(f"  folds at/below 50th pct: {sum(1 for p in percentiles if p <= 50)}/{len(percentiles)}")
        print(f"WITHIN-TERRAIN percentile ranks (same terrain_type only): mean={np.mean(within):.1f} "
              f"median={np.median(within):.1f} min={np.min(within):.1f} max={np.max(within):.1f}")
        print(f"  folds at/below 50th pct within-terrain: {sum(1 for p in within if p <= 50)}/{len(within)}")
        for r in results:
            print(f"  way={r.held_out_way_id} segs={r.held_out_segment_ids} year={r.year} terrain={r.terrain_type} "
                  f"full_pctile={r.percentile_rank:.1f} within_terrain_pctile={r.within_terrain_percentile_rank:.1f}")

    section("5. LEAVE-ONE-GROUP-OUT -- Logistic Regression")
    logo_lr_results, logo_lr_pooled_auc = leave_one_group_out(fm, make_logistic_regression)
    report_logo(logo_lr_results, logo_lr_pooled_auc, "Logistic Regression")

    section("6. LEAVE-ONE-GROUP-OUT -- Random Forest")
    logo_rf_results, logo_rf_pooled_auc = leave_one_group_out(fm, make_random_forest)
    report_logo(logo_rf_results, logo_rf_pooled_auc, "Random Forest")

    section("7. FEATURE IMPORTANCE (fit on FULL data -- interpretability only, NOT the LOGO-evaluated models)")
    full_lr = make_logistic_regression()
    full_lr.fit(fm.X, fm.y_pseudo)
    print("Logistic Regression standardized coefficients:")
    for name, coef in logistic_regression_coefficients(full_lr, fm.feature_names):
        print(f"  {name}: {coef:+.3f}")

    full_rf = make_random_forest()
    full_rf.fit(fm.X, fm.y_pseudo)
    print("\nRandom Forest impurity-based importances:")
    for name, imp in random_forest_importances(full_rf, fm.feature_names):
        print(f"  {name}: {imp:.3f}")

    section("8. BASELINE PERCENTILE RANKS (for direct comparison against LOGO ML results above)")
    baseline_percentiles = []
    for r in baseline_results:
        baseline_percentiles.extend(r.percentile_ranks)
    print(f"Baseline percentile ranks: mean={np.mean(baseline_percentiles):.1f} median={np.median(baseline_percentiles):.1f} "
          f"min={np.min(baseline_percentiles):.1f} max={np.max(baseline_percentiles):.1f}")

    section("9. LEAKAGE CHECKS")
    for check in run_all_checks(df, fm):
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}\n    {check.detail}")

    print("\nDone. No model was trained for production use; nothing here was integrated into the running app.")


if __name__ == "__main__":
    main()
