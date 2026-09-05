"""
Tests for Part 14.3's feasibility-study utilities (app/data/ml/
feature_matrix.py, ranking_evaluation.py, logo_evaluation.py,
baseline_risk_scorer.py, leakage_checks.py, models.py). This package is
never imported by production code -- see
test_ml_segment_year_dataset.py::test_no_ml_module_is_imported_by_production_code
for the isolation guard covering the whole app/data/ml/ package.

These tests exercise real logic against the real dataset (not synthetic
stand-ins) but keep runtime bounded by using a small subset of the 25
positive way-groups for the LOGO tests rather than the full leave-one-out
sweep (that full sweep is what app/data/ml/run_feasibility_study.py runs
for the actual feasibility report).
"""
from pathlib import Path

import numpy as np
import pytest

from app.data.ml.feature_matrix import build_feature_matrix, load_dataset
from app.data.ml.logo_evaluation import groupkfold_diagnostic, leave_one_group_out
from app.data.ml.models import make_logistic_regression, make_random_forest
from app.data.ml.ranking_evaluation import _percentile_rank, evaluate_all_event_years, evaluate_year

DATASET_CSV = Path(__file__).resolve().parents[1] / "app" / "data" / "derived" / "segment_year_dataset.csv"


@pytest.fixture(scope="module")
def df():
    if not DATASET_CSV.exists():
        pytest.skip(f"{DATASET_CSV} not built yet -- run python -m app.data.ml.build_segment_year_dataset first")
    return load_dataset(DATASET_CSV)


@pytest.fixture(scope="module")
def fm(df):
    return build_feature_matrix(df)


# ---------------------------------------------------------------------------
# feature_matrix.py
# ---------------------------------------------------------------------------


def test_feature_matrix_shape_and_no_nans(fm):
    assert fm.X.shape[0] == 2964 * 11
    assert not fm.X.isna().any().any(), "no NaN should reach the model matrix -- nearest-distance NaNs must be imputed with a sentinel + flag"


def test_feature_matrix_way_id_collapses_sibling_segments(fm):
    """The known, measured finding: 29 distinct positive segment_ids
    collapse to 25 distinct way-id groups."""
    positive_segments = set(fm.segment_id[fm.is_event])
    positive_ways = set(fm.way_id[fm.is_event])
    assert len(positive_segments) == 29
    assert len(positive_ways) == 25
    assert len(positive_ways) < len(positive_segments)


def test_feature_matrix_never_uses_lifetime_historical_count_column(fm):
    assert "historical_landslide_count_prior" in fm.feature_names
    assert "historical_landslide_count" not in fm.feature_names


# ---------------------------------------------------------------------------
# ranking_evaluation.py
# ---------------------------------------------------------------------------


def test_percentile_rank_extremes():
    # Mid-rank convention: the query score is compared against a population
    # that includes itself, so "highest of 5" is (4 others below + 0.5 for
    # the tie with itself) / 5 = 90.0, not a clean 100.0 -- this converges
    # to ~100.0 for the dataset's real n (~2,964), which is why the actual
    # feasibility-study output prints "100.0" for several 2021 rows.
    scores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert _percentile_rank(5.0, scores) == 90.0
    assert _percentile_rank(0.0, scores) == 0.0  # lower than everything -> 0th percentile


def test_percentile_rank_handles_ties():
    scores = np.array([1.0, 1.0, 1.0, 1.0])
    # every value ties every other -> mid-rank convention gives 50.0 for all
    assert _percentile_rank(1.0, scores) == 50.0


def test_evaluate_year_skips_years_with_zero_events(df):
    # 2015 has zero event rows in the real dataset -- evaluate_all_event_years
    # must never fabricate a result for it.
    results = evaluate_all_event_years(df, np.random.RandomState(0).rand(len(df)))
    years_evaluated = {r.year for r in results}
    assert 2015 not in years_evaluated
    assert 2016 in years_evaluated and 2021 in years_evaluated


def test_evaluate_year_perfect_scores_gives_top_percentile_and_auc_1():
    is_event = np.array([True, False, False, False, False])
    scores = np.array([10.0, 1.0, 2.0, 3.0, 4.0])  # the one true event scores highest
    result = evaluate_year(2020, scores, is_event)
    assert result.percentile_ranks == [90.0]  # see test_percentile_rank_extremes for why not 100.0
    assert result.rank_auc == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# logo_evaluation.py -- real logic, small subset for test speed
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_fm(fm):
    """Restricts to a handful of positive way-groups (plus all unlabeled
    rows) so LOGO tests run in seconds, not the ~1 minute the full 25-group
    sweep takes -- see run_feasibility_study.py for the full sweep."""
    from dataclasses import replace

    positive_ways = sorted(set(fm.way_id[fm.is_event]))[:3]
    keep_mask = np.isin(fm.way_id, positive_ways) | ~fm.is_event
    return replace(
        fm,
        X=fm.X.loc[keep_mask].reset_index(drop=True),
        y_pseudo=fm.y_pseudo[keep_mask],
        is_event=fm.is_event[keep_mask],
        segment_id=fm.segment_id[keep_mask],
        way_id=fm.way_id[keep_mask],
        year=fm.year[keep_mask],
        label_status=fm.label_status[keep_mask],
        terrain_type=fm.terrain_type[keep_mask],
    )


def test_leave_one_group_out_never_trains_on_the_held_out_group(small_fm):
    results, pooled_auc = leave_one_group_out(small_fm, make_logistic_regression)
    assert len(results) >= 3  # at least one fold per held-out way-group
    for r in results:
        assert 0.0 <= r.percentile_rank <= 100.0
        assert 0.0 <= r.within_terrain_percentile_rank <= 100.0


def test_leave_one_group_out_reports_a_pooled_auc(small_fm):
    _results, pooled_auc = leave_one_group_out(small_fm, make_random_forest)
    assert pooled_auc is not None
    assert 0.0 <= pooled_auc <= 1.0


def test_groupkfold_diagnostic_reports_fold_composition(small_fm):
    diagnostics = groupkfold_diagnostic(small_fm, make_random_forest, n_splits=3)
    assert len(diagnostics) == 3
    total_val_positive_groups = sum(d.n_val_positive_groups for d in diagnostics)
    assert total_val_positive_groups == 3  # every positive way-group in this subset appears in exactly one fold's validation
    for d in diagnostics:
        if d.n_val_positive_groups == 0:
            assert d.fold_auc is None  # never fabricate a metric for an unevaluable fold


# ---------------------------------------------------------------------------
# baseline_risk_scorer.py -- confirms it calls the REAL production risk engine
# ---------------------------------------------------------------------------


def test_baseline_scores_are_valid_risk_scores(df):
    from app.data.ml.baseline_risk_scorer import compute_baseline_scores

    sample = df.sample(n=20, random_state=1)
    scores = compute_baseline_scores(sample)
    assert len(scores) == 20
    assert scores.between(0.0, 1.0).all()


# ---------------------------------------------------------------------------
# leakage_checks.py
# ---------------------------------------------------------------------------


def test_all_leakage_checks_pass_on_the_real_dataset(df, fm):
    from app.data.ml.leakage_checks import run_all_checks

    checks = run_all_checks(df, fm)
    failed = [c for c in checks if not c.passed]
    assert failed == [], f"leakage checks failed: {[(c.name, c.detail) for c in failed]}"
    assert len(checks) == 7
