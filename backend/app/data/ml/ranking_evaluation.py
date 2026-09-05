"""
Part 14.3: ranking-only evaluation utilities, shared by the baseline
scorer and every trained model so both are judged by the exact same
protocol (Step 8 requires a fair comparison).

--- Why ranking metrics, never accuracy/precision/recall/F1 ---

Every "0" in this dataset is `unobserved`, not a confirmed non-event (see
app/data/ml_dataset_inspection_part14.md). Computing accuracy/precision/
recall/F1 would silently treat every unobserved row as ground-truth
negative -- exactly the fabrication the task prohibits. Instead, every
metric here answers a strictly weaker, honest question: "among this year's
segments, does a real documented event score higher than an unlabeled
segment more often than chance?" That is a ranking question, not a
classification-accuracy question, and it never requires believing an
unlabeled row is actually safe -- it only compares relative scores.

`same_year_rank_auc` reuses the AUC/Mann-Whitney U statistic purely as a
rank-based effect size (P(score(known event) > score(random other segment,
same year))) -- the "0" group is always described as "unlabeled
comparison group," never "negative," in every docstring/report string this
module produces.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


@dataclass
class YearRankingResult:
    year: int
    n_events: int
    n_unlabeled: int
    percentile_ranks: list[float]  # one per event row that year, 0-100 (100 = highest score in the year)
    rank_auc: Optional[float]  # None if not computable (e.g. zero variance, or <1 event)


def _percentile_rank(score: float, all_scores_same_year: np.ndarray) -> float:
    """Percentage of same-year rows this score is >= to (ties counted as
    half-credit, the standard mid-rank convention) -- 100.0 means the
    highest score among all segments scored that year."""
    n = len(all_scores_same_year)
    greater = (all_scores_same_year < score).sum()
    equal = (all_scores_same_year == score).sum()
    return 100.0 * (greater + 0.5 * equal) / n


def evaluate_year(year: int, scores: np.ndarray, is_event: np.ndarray) -> YearRankingResult:
    """
    `scores`/`is_event` must both be restricted to rows for ONE year,
    already aligned. Computes each event row's percentile rank among that
    full year's segment population, and the pooled rank-AUC for that year.
    """
    event_scores = scores[is_event]
    n_events = len(event_scores)
    n_unlabeled = len(scores) - n_events

    percentile_ranks = [_percentile_rank(s, scores) for s in event_scores]

    rank_auc = None
    if n_events >= 1 and n_unlabeled >= 1 and len(np.unique(scores)) > 1:
        # y=1 for the (small number of) real documented events this year,
        # y=0 for every OTHER segment that year -- used here strictly as
        # the AUC statistic's reference population, not as an assertion
        # that those segments are confirmed landslide-free (see module
        # docstring).
        rank_auc = float(roc_auc_score(is_event.astype(int), scores))

    return YearRankingResult(
        year=year, n_events=n_events, n_unlabeled=n_unlabeled,
        percentile_ranks=percentile_ranks, rank_auc=rank_auc,
    )


def evaluate_all_event_years(df: pd.DataFrame, scores: np.ndarray) -> list[YearRankingResult]:
    """Runs evaluate_year() for every year that has >=1 real event row --
    years with zero events are not evaluable (nothing to rank against) and
    are simply skipped, never scored as if evaluated."""
    results = []
    is_event_all = (df["label_status"].to_numpy() == "event")
    for year in sorted(df["year"].unique()):
        year_mask = (df["year"].to_numpy() == year)
        if is_event_all[year_mask].sum() == 0:
            continue
        results.append(evaluate_year(int(year), scores[year_mask], is_event_all[year_mask]))
    return results


def summarize_results(results: list[YearRankingResult], label: str) -> str:
    lines = [f"### {label}", ""]
    lines.append("| year | n_events | n_unlabeled | rank_auc | event percentile ranks |")
    lines.append("|---|---|---|---|---|")
    all_percentiles: list[float] = []
    all_aucs: list[float] = []
    for r in results:
        aucs = f"{r.rank_auc:.3f}" if r.rank_auc is not None else "n/a"
        percentiles_str = ", ".join(f"{p:.1f}" for p in r.percentile_ranks)
        lines.append(f"| {r.year} | {r.n_events} | {r.n_unlabeled} | {aucs} | {percentiles_str} |")
        all_percentiles.extend(r.percentile_ranks)
        if r.rank_auc is not None:
            all_aucs.append(r.rank_auc)
    lines.append("")
    if all_percentiles:
        lines.append(
            f"- Mean event percentile rank across all evaluable years: "
            f"**{np.mean(all_percentiles):.1f}** (100 = always scored highest that year, "
            f"50 = indistinguishable from a random segment that year)"
        )
    if all_aucs:
        lines.append(f"- Mean per-year rank-AUC: **{np.mean(all_aucs):.3f}**")
    return "\n".join(lines)
