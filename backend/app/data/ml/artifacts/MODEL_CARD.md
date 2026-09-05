# Model Card — Segment-Year Landslide Ranking Prototype

**Experiment ID:** `part14_segment_year_v1` (Part 14.4)
**Models:** Logistic Regression (`logistic_regression_model.joblib`),
Random Forest (`random_forest_model.joblib`)
**Status:** Prototype research artifact. **Not integrated into production**
(not imported by `app/core/`, `app/api/`, or `app/simulation/`).

---

## Intended use

Prototype **road-segment risk *ranking*** for the NER logistics research
system — i.e. "given a set of segments, which ones does this model rank as
relatively more concerning," for research and further evaluation only.

## Not intended for

- **Autonomous safety decisions.** No hazard, closure, or reroute decision
  should be driven by these models' output.
- **Claiming calibrated landslide probabilities.** A score from either
  model is not a probability of a landslide occurring. Neither model was
  calibrated (e.g. via Platt scaling/isotonic regression) against a
  trustworthy base rate — and no trustworthy base rate exists in this
  dataset to calibrate against in the first place (see Labels, below).
- **Replacing domain experts.** GSI/APSAC/local engineering judgment
  remains the authoritative source on landslide hazard for this corridor.
- **Production deployment without additional validation.** See
  Limitations.

## Training data

`backend/app/data/derived/segment_year_dataset.csv` — 32,604 rows: every
real road segment currently loaded (2,964, from the real OSM
Guwahati–Tawang corridor extract) crossed with every year the real IMD
rainfall archive covers (2015–2025, 11 years). Every feature is either a
real per-segment DEM measurement (slope, elevation), a real OSM attribute
(road type, distance), a real IMD rainfall aggregate for that segment's
nearest 0.25° grid cell in that specific year, or a real GSI-landslide-derived
count/distance computed with a strict "prior years only" cutoff (never
including the row's own or a future year's record). Full provenance and
audit: `app/data/ml_dataset_inspection_part14.md` and
`app/data/derived/segment_year_dataset_audit.md`.

## Labels

- **30 documented `event` rows** (label = 1): a real GSI landslide record
  matched (within 500m) to a real road segment, in the segment's own event
  year — 9 in 2016, 21 in 2021. 29 distinct segments, collapsing to **25
  distinct physical roads** once OSM way-splits are accounted for.
- **0 documented `non_event_documented` rows.** The code that would assign
  this label (a genuine negative/monitoring observation, e.g. "surveyed,
  found stable") exists and was checked against the real GSI free text —
  zero records qualify. This is a measured fact about the source data, not
  an unimplemented feature.
- **32,574 `unobserved` rows** (label = NaN, not 0): no GSI record exists
  for that segment in that year.

**`unobserved` is never the same as `safe`.** The GSI inventory is an
opportunistic field record, not a systematic survey of every segment every
year — 98.9% of segments have zero recorded history at all, which reflects
observation coverage, not confirmed absence of hazard. Both models were
fit by treating `unobserved` rows as 0 **for optimization purposes only**
(a standard, explicitly-flagged positive-unlabeled learning simplification)
— **every reported evaluation metric is a ranking metric against the real
event/unobserved distinction, never an accuracy/precision/recall/F1 score
that would require trusting `unobserved` as ground truth.**

## Validation

**Leave-one-way-group-out (LOGO):** 25 folds, each holding out one entire
physical road (all its `RoadSegment` pieces, all 11 years) from training,
scoring only that held-out group's own real event year(s) against a model
that never saw it. Grouped by **OSM way_id**, not raw `segment_id` —
measured that 29 positive segment_ids collapse to 25 way-id groups (one
physical road is often split into 2–3 `RoadSegment` rows). Every fold is
evaluable by construction (never zero held-out positives).

**Reported generalization estimate** (from `validation_metadata.json`,
reproduced fresh at artifact-save time and matching the Part 14.3 report
exactly): mean within-terrain percentile rank **72.9 (Logistic
Regression)** / **78.6 (Random Forest)** — see "within-terrain" note below.

**Important: this is NOT the saved models' own score.** The two `.joblib`
files in this directory were fit on ALL 25 positive groups (the standard
"refit on everything once validation is done" step) — they have no honest
held-out score of their own. The 72.9/78.6 numbers describe 25 *different*,
transient, per-fold models used only for evaluation. Do not conflate the
two.

**"Within-terrain" — why this number, not the flashier one:** a naive
full-population evaluation gives misleadingly high numbers (~95–97th
percentile) because ~90% of positives fall on just 9.1% of the corridor
(mountain/hill terrain) — any model that merely recognizes "mountain road"
scores well there without learning anything about *which* mountain road is
riskiest. The within-terrain number restricts the comparison to segments
sharing the same terrain class, which is the honest question this
prototype can actually answer something about.

## Limitations

1. **Only 25 independent positive physical-road groups.** Every metric in
   this card/manifest is an average over a handful of independent
   observations; no confidence interval is reported because none would be
   trustworthy at this sample size.
2. **Reporting/observation-location bias is unresolved.** Nothing in this
   data can separate "the high-mountain section genuinely has more
   landslides" from "the high-mountain section is more frequently
   surveyed." Both models likely reflect some mix of both.
3. **Sparse event labels, clustered in time and space.** Only 2 of 11
   years (2016, 2021) contain any positive label; only 6 of 30 positives
   have a day-precise date (all in 2016). 2021's 21 positives are
   year-precision only.
4. **0.25° rainfall grid resolution** (~25–28km cells) is far coarser than
   road-segment spacing — many segments share identical rainfall features
   on a given day.
5. **Temporal limitation:** rainfall features are for the row's own
   calendar year, not strictly pre-event, for every 2021 positive (no
   day-precise date exists to split "before" from "after").
6. **No confirmed negative observations exist anywhere in this dataset.**
   `non_event_documented` is empty by measurement (see Labels). This is the
   single biggest reason these models cannot be treated as reliable.
7. **Logistic Regression coefficients are not reliably interpretable** at
   this sample size — see `feature_importance.json`'s caveats; the
   largest-magnitude coefficient has a sign that does not support a causal
   reading (a known multicollinearity artifact, not a finding).
8. Neither model's feature importance supports a **causal** claim about
   what "causes" landslides — both reflect correlation with a small,
   geographically clustered, non-randomly-observed label set.

## Conclusion (unchanged from Part 14.3)

**ML PROTOTYPE POSSIBLE BUT NOT RELIABLE FOR PRODUCTION.** These artifacts
exist so the already-reported experiment can be reloaded and inspected
without retraining — not as a signal that the conclusion has changed.
