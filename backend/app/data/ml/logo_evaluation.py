"""
Part 14.3: grouped, leakage-safe validation (Step 5). Two protocols are
implemented and BOTH are reported, deliberately:

1. `groupkfold_diagnostic()` -- plain scikit-learn GroupKFold(n_splits=5),
   grouped by `way_id` (see feature_matrix.py for why way_id, not
   segment_id). Reported ONLY as a diagnostic of why an ordinary small-k
   grouped split is fragile here: with 25 independent positive way-groups
   split across 5 folds, each fold gets ~5 positive groups by chance, and
   which 5 matters enormously for such a small sample -- this function
   reports the real per-fold positive-group counts and per-fold rank-AUC
   so that fragility is demonstrated with actual numbers, not asserted.

2. `leave_one_group_out()` -- the PRIMARY evaluation used for every result
   in the feasibility report. Holds out exactly one way-group (all its
   rows, all years) at a time, fits on everything else, and scores the
   held-out group's own real event year(s) against that year's full
   segment population under a model that never saw the held-out group.
   With 25 positive way-groups, this is 25 folds, each with EXACTLY one
   held-out positive group -- every fold is evaluable by construction
   (never zero positives in validation), which is the property
   GroupKFold(5) above cannot guarantee.

Neither function ever reports accuracy/precision/recall/F1 -- only
ranking metrics (see ranking_evaluation.py) against the real
`unobserved` comparison population, never against a fabricated negative.
"""
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

from app.data.ml.feature_matrix import FeatureMatrix
from app.data.ml.ranking_evaluation import _percentile_rank

ModelFactory = Callable[[], object]  # returns an unfit sklearn-style estimator


@dataclass
class LogoFoldResult:
    held_out_way_id: str
    held_out_segment_ids: list[str]
    year: int
    percentile_rank: float  # 0-100 among that year's full population, under a model that never saw this group
    terrain_type: str
    within_terrain_percentile_rank: float  # 0-100 among ONLY that year's same-terrain_type segments -- see
    # module docstring "Why a within-terrain check matters" for why this second number is reported
    # alongside every fold, not as an afterthought.


def leave_one_group_out(fm: FeatureMatrix, make_model: ModelFactory) -> tuple[list[LogoFoldResult], Optional[float]]:
    positive_way_ids = sorted(set(fm.way_id[fm.is_event]))
    results: list[LogoFoldResult] = []

    # Pooled out-of-fold scores, for one headline AUC across all folds --
    # label=1 only for the CURRENTLY held-out fold's own event row(s);
    # label=0 only for that year's `unobserved` rows (never another real
    # event row) scored by that SAME fold's model. See module docstring.
    pooled_labels: list[int] = []
    pooled_scores: list[float] = []

    for way in positive_way_ids:
        held_out_mask = (fm.way_id == way)
        train_mask = ~held_out_mask

        model = make_model()
        model.fit(fm.X.loc[train_mask], fm.y_pseudo[train_mask])

        held_out_segment_ids = sorted(set(fm.segment_id[held_out_mask & fm.is_event]))
        held_out_years = sorted(set(fm.year[held_out_mask & fm.is_event]))

        for year in held_out_years:
            year_mask = (fm.year == year)
            year_scores = model.predict_proba(fm.X.loc[year_mask])[:, 1]

            # A way-group can contribute MORE THAN ONE row in the same year
            # (a physical road split into multiple RoadSegment pieces, e.g.
            # way 238496657 -> 3 segments, all matched to nearby 2021 GSI
            # records) -- evaluate every one of the group's own rows that
            # year, not just a single assumed row.
            year_way = fm.way_id[year_mask]
            year_terrain = fm.terrain_type[year_mask]
            held_out_row_idx = np.where(year_way == way)[0]
            assert len(held_out_row_idx) >= 1, "expected at least one row for this way-group in this year"

            year_unobserved_mask = (fm.label_status[year_mask] == "unobserved")

            for idx in held_out_row_idx:
                held_out_score = year_scores[idx]
                percentile = _percentile_rank(held_out_score, year_scores)

                # Why a within-terrain check matters: >=90% of positives fall
                # on the ~9% of segments that are mountain/hill terrain (see
                # ml_modeling_feasibility_part14.md). A score that merely
                # recognizes "mountain road" scores near the top of the WHOLE
                # corridor almost for free -- the within-terrain percentile
                # instead asks "does it discriminate risk AMONG segments that
                # already share this segment's terrain class," which is the
                # much harder, more meaningful question.
                same_terrain_mask = (year_terrain == year_terrain[idx])
                within_terrain_percentile = _percentile_rank(held_out_score, year_scores[same_terrain_mask])

                results.append(LogoFoldResult(
                    held_out_way_id=way, held_out_segment_ids=held_out_segment_ids,
                    year=int(year), percentile_rank=percentile,
                    terrain_type=str(year_terrain[idx]),
                    within_terrain_percentile_rank=within_terrain_percentile,
                ))
                pooled_labels.append(1)
                pooled_scores.append(float(held_out_score))

            pooled_labels.extend([0] * int(year_unobserved_mask.sum()))
            pooled_scores.extend(year_scores[year_unobserved_mask].tolist())

    pooled_auc = None
    if len(set(pooled_labels)) > 1:
        pooled_auc = float(roc_auc_score(pooled_labels, pooled_scores))

    return results, pooled_auc


@dataclass
class GroupKFoldDiagnostic:
    fold_index: int
    n_train_positive_groups: int
    n_val_positive_groups: int
    n_val_rows: int
    fold_auc: Optional[float]  # None if this fold's validation set has <2 classes present


def groupkfold_diagnostic(fm: FeatureMatrix, make_model: ModelFactory, n_splits: int = 5) -> list[GroupKFoldDiagnostic]:
    """Ordinary GroupKFold(n_splits), grouped by way_id -- see module
    docstring for why this is reported only as a diagnostic, not the
    primary evaluation."""
    gkf = GroupKFold(n_splits=n_splits)
    diagnostics = []
    for i, (train_idx, val_idx) in enumerate(gkf.split(fm.X, fm.y_pseudo, groups=fm.way_id)):
        n_train_pos_groups = len(set(fm.way_id[train_idx][fm.is_event[train_idx]]))
        n_val_pos_groups = len(set(fm.way_id[val_idx][fm.is_event[val_idx]]))

        fold_auc = None
        if n_val_pos_groups > 0:
            model = make_model()
            model.fit(fm.X.iloc[train_idx], fm.y_pseudo[train_idx])
            val_scores = model.predict_proba(fm.X.iloc[val_idx])[:, 1]
            val_is_event = fm.is_event[val_idx]
            if len(set(val_is_event)) > 1:
                fold_auc = float(roc_auc_score(val_is_event.astype(int), val_scores))

        diagnostics.append(GroupKFoldDiagnostic(
            fold_index=i, n_train_positive_groups=n_train_pos_groups,
            n_val_positive_groups=n_val_pos_groups, n_val_rows=len(val_idx), fold_auc=fold_auc,
        ))
    return diagnostics
