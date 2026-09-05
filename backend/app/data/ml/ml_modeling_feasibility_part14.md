# ML Modeling Feasibility & Baseline — Part 14.3

Status: **feasibility study only.** No model, weight, or score from this
document has been integrated into `app/core/risk_engine.py`,
`routing_engine.py`, `reroute_service.py`, `app/api/`, `app/simulation/`, or
the frontend. Every number below was produced by running the real code in
`backend/app/data/ml/` against the real, audited
`backend/app/data/derived/segment_year_dataset.csv` — nothing here is
estimated or narrated without having actually been computed
(`python -m app.data.ml.run_feasibility_study`).

---

## 1. Dataset summary

- **32,604 rows** = 2,964 real segments × 11 years (2015–2025), exactly as
  built and audited in Part 14/14.2.
- `label_status`: **30 `event`**, **0 `non_event_documented`**, **32,574
  `unobserved`** — matches the Part 14.2 audit exactly.
- Missing values: `landslide_hazard_score` 32,604/32,604 (100% — APSAC
  still unavailable, unchanged), `nearest_historical_landslide_distance_m_prior`
  32,397/32,604 (no prior-dated match yet for that segment-year — expected,
  not an error), `label` 32,574/32,604 (NaN for every `unobserved` row, by
  design).
- **Event rows by year: 2016 → 9, 2021 → 21.** No events in any other year
  in this table (2008/2014 records predate rainfall coverage and are
  outside the 2015–2025 table range entirely; see Part 14 inspection).
- **29 distinct positive `segment_id`s** — but see §6.7: these collapse to
  **25 distinct OSM way-id groups** once physical road splits are
  accounted for. This is the number that matters for validation design.
- Descriptive comparison (n=30, **not a significance test**): event rows
  average **slope 7.52° vs 2.14° corridor-wide**, **elevation 1,899m vs
  252m corridor-wide** — a large, real difference. Point-biserial
  correlations with `is_event` are small in absolute terms (elevation
  0.076, slope 0.063, monsoon rainfall 0.034, historical count 0.021,
  annual rainfall 0.020) because 30 positives among 32,604 rows mechanically
  caps how large any linear correlation coefficient can get — the
  *practically meaningful* signal is in the group-mean gap, not the
  correlation coefficient's magnitude.
- No feature column is NaN-free-but-wrong: a random 25-row re-derivation of
  `annual_rainfall_mm` directly from each row's own year's NetCDF file
  matched exactly (§9).

## 2. Label problem

Restated plainly: this is **not** a conventional labeled dataset.
`event` means a real, GSI-documented landslide matched to a real road
segment in a specific year. `unobserved` means **no evidence either way** —
not "confirmed safe." `non_event_documented` exists as a real code path
(checked against genuine negative-observation language — "stable,"
"surveyed," "no fresh movement," etc. — in the raw GSI free text) but is
**measured to be empty**: zero of the 104 matched GSI records contain any
such language. There is currently no source of confirmed negative
(landslide-free) observations in this dataset at all.

## 3. Recommended ML framing

Five options were weighed against the actual constraints (30 positive
rows, 25 independent positive groups, zero confirmed negatives, strong
reporting-location bias, event clustering in 2 of 11 years, a 0.25°
rainfall grid far coarser than segment spacing, and — newly measured in
this study — **90% of positives falling on just 9.1% of the corridor's
segments** by terrain class):

| Option | Fit for this data | Why |
|---|---|---|
| **A. Conventional binary classification** | ✗ Rejected as the primary framing | Requires trusting `unobserved` as negative, which the task explicitly forbids and which Part 14 already showed is unjustified (reporting/observation bias, not a systematic survey). Any accuracy/precision/recall/F1 number would be a number about how well the model reproduces *where GSI happened to look*, not landslide risk. |
| **B. Positive-Unlabeled (PU) learning** | ⚠️ Conceptually the right family, but its core assumption doesn't hold | Classic PU methods (e.g. Elkan-Noto) assume positives are a random sample of the true positive population (SCAR). Here they are not: positives cluster near named towns/circuit houses and high-mountain sections that are simply easier to survey. Using PU machinery would produce a confident-looking but still-biased result — better than pretending unlabeled = negative, but not a fix for the underlying bias. |
| **C. Anomaly/outlier scoring** | ✅ Usable, but redundant with D | Unsupervised scoring (e.g. isolation forest on static features) avoids the negative-label problem entirely by never using labels for training, only for validation. Given only 21 numeric+categorical features and a formula-based baseline that already does this job explainably, a separate unsupervised anomaly model would add complexity without a clear incremental benefit over D below. |
| **D. Ranking of higher-risk segments** | ✅ **Adopted as the primary framing, for both baseline and ML** | Sidesteps the negative-label problem completely: the only question ever asked is "does a real documented event score higher than an unlabeled segment, more often than chance?" This never requires believing an unlabeled row is safe — it only compares relative scores. It is also the only framing directly usable by the existing routing/risk product (ranking road segments by relative risk is exactly what `risk_engine.py`/`routing_engine.py` already do). |
| **E. Other (e.g. spatial hotspot / kernel density)** | Not pursued | A legitimate alternative worth a future look (it would explicitly model spatial clustering rather than incidentally absorbing it through `elevation_m`/`terrain_type`), but out of scope for this pass — flagged as a follow-up in §11. |

**Recommendation:** frame every evaluation in this study as **ranking**,
never classification. This is implemented in `ranking_evaluation.py`
(percentile rank + AUC-as-rank-statistic, always against an explicitly
named "unlabeled comparison group," never "negative").

## 4. Baseline methodology

The baseline reuses the **real, unmodified** production
`app/core/risk_engine.py::assess_segment_risk()` — not a re-implementation.
For each segment-year row, `app/data/ml/baseline_risk_scorer.py` builds a
`RoadSegment.model_copy(update=...)` with only
`historical_landslide_count`/`nearest_landslide_distance_m` swapped for
that row's strict prior-cutoff values (§14.2); `slope_deg`, `elevation_m`,
`terrain_type` are real, time-invariant, and passed through unchanged.
`weather_factor` is derived by running the row's `max_daily_rainfall_mm`
(that year's single wettest real day at the segment's grid cell) through
the real, unmodified `app/core/weather_factor.py::rainfall_mm_to_weather_factor()`
— an explicit, documented proxy for a true same-day reading (see
Limitations, §11), not a new formula.

**Baseline result, full population:**

| year | n_events | n_unlabeled | rank-AUC | mean event percentile |
|---|---|---|---|---|
| 2016 | 9 | 2,955 | 0.903 | ~90 |
| 2021 | 21 | 2,943 | 0.971 | ~96 |

Pooled: **mean percentile 94.8, mean rank-AUC 0.937.**

**This number is misleading on its own.** Restricting the comparison to
only mountain+hill segments (2,959 of 32,604 rows, 9.1% of the corridor,
containing 27 of 30 events) — i.e. asking "does the baseline discriminate
risk *among* segments that already share the same terrain class?" — the
score **collapses to chance**:

| year (mountain+hill only) | n_events | n_unlabeled | rank-AUC | mean event percentile |
|---|---|---|---|---|
| 2016 | 8 | 261 | 0.399 | ~50 |
| 2021 | 19 | 250 | 0.670 | ~64 |

Pooled: **mean percentile 58.2, mean rank-AUC 0.535** — barely above 50/0.5
(no discrimination). **Conclusion: essentially all of the baseline's
apparent ranking skill comes from recognizing "this is a mountain/hill
road," a fact already fully known from static DEM terrain classification
with zero rainfall or landslide-history data required — not from
discriminating which specific mountain road is riskiest.**

## 5. ML models attempted

Exactly two, per the task's instruction not to search for a leaderboard
score:

- **Logistic Regression** (`StandardScaler` + `class_weight="balanced"`,
  `max_iter=2000`) — chosen for coefficient interpretability.
- **Random Forest** (`n_estimators=300, max_depth=5, min_samples_leaf=5,
  class_weight="balanced_subsample"`) — chosen for non-linear interactions
  and a second, independent interpretability lens (impurity importance).

**Gradient boosting/XGBoost (available in this environment) was
deliberately not used.** With only 25 independent positive groups, a
higher-capacity boosted-tree model has more room to fit noise than to find
generalizable signal, and would add a third model's worth of interpretive
burden without a credible way to validate the extra complexity was
warranted. No hyperparameter search was run for either model used —
`class_weight="balanced"` is the one deliberate, non-tuned concession to
severe imbalance.

Every row's `unobserved` status is treated as 0 **for model fitting only**
(the standard, explicitly-flagged PU-learning simplification) — every
reported *evaluation* metric is a ranking metric against the real
`event`/`unobserved` distinction, never accuracy/precision/recall/F1
against that pseudo-label.

## 6. Validation methodology

**Grouped by OSM way-id, never by raw `segment_id` and never by random row
split.** A single physical road is frequently split into multiple
`RoadSegment` rows; checked directly: the 29 positive `segment_id`s
collapse to **25 distinct way-id groups** (3 way-ids each contribute 2–3
"different" positive segments representing the same physical stretch,
e.g. `seg_238496657_1/_2/_4`). Every grouped split in this study uses
way-id.

**Two protocols were run, deliberately:**

**(a) GroupKFold(5) — reported only as a diagnostic**, to show concretely
why an ordinary small-k grouped split is fragile at this sample size:

| fold | train positive groups | val positive groups | val rows | fold AUC |
|---|---|---|---|---|
| 0 | 17 | 8 | 6,523 | 0.994 |
| 1 | 20 | 5 | 6,523 | 0.995 |
| 2 | 22 | 3 | 6,523 | 0.991 |
| 3 | 19 | 6 | 6,523 | 0.966 |
| 4 | 22 | 3 | 6,512 | 0.997 |

All 5 folds happened to be evaluable this run (every fold got ≥3 positive
groups), but with only 25 groups spread across 5 folds by chance, a fold
with 0 or 1 positive groups is entirely plausible on a different random
seed/fold assignment — this diagnostic is reported to show the *fold
composition*, not to be trusted as the headline number (and, per §7/§8, the
uniformly high AUCs here are themselves mostly explained by the same
terrain-recognition effect that inflated the baseline).

**(b) Leave-one-way-group-out (LOGO) — the primary evaluation.** 25 folds,
each holding out exactly one way-group (all its rows, all years) from
training. **Every fold is evaluable by construction** (exactly one held-out
positive group's real event-year(s) per fold, never zero) — this is
reported explicitly because the task requires stating so, not assumed.
38 total (way-group, year) evaluations came out of the 25 folds (some
way-groups have multiple segments and/or multiple event years).

- **Training positives per fold:** 28 (29 total positive segments minus
  the ~1 held out — since grouping is by way, holding out one way-group
  removes 1–3 segments but always leaves the other 24 way-groups' full
  segment set in training).
- **Validation positives per fold:** exactly the held-out way-group's own
  event row(s) (1–6 rows depending on how many segments/years that way-group
  covers) — never zero, satisfying the "is every fold evaluable" requirement.

## 7. Evaluation results

**Full-population LOGO results** (same misleading-if-read-alone caveat as
the baseline applies):

| model | pooled AUC | mean percentile | median | min | max |
|---|---|---|---|---|---|
| Logistic Regression | 0.954 | 96.1 | 97.5 | 76.5 | 99.9 |
| Random Forest | 0.981 | 97.5 | 98.1 | 92.1 | 99.9 |

**Within-terrain LOGO results** (the honest question: does the model
discriminate risk among segments sharing the held-out row's own terrain
class, using a model that never saw that row):

| model | mean within-terrain percentile | median | min | max | folds ≤ 50th pct |
|---|---|---|---|---|---|
| Baseline (no ML, for reference) | 58.2 | — | — | — | most of 27 |
| Logistic Regression | **72.9** | 75.9 | 27.9 | 99.9 | 5 / 38 |
| Random Forest | **78.6** | 81.2 | 23.4 | 99.9 | 4 / 38 |

**This is the central finding of this study.** Both ML models retain
*meaningfully more* within-terrain discriminative power than the existing
explainable baseline (73–79th percentile mean vs. 58th, which is
essentially chance) — a genuine, positive, honestly-measured signal that
ML may be picking up something (most plausibly rainfall and/or road-class
interactions — see §8) the current static terrain-only formula misses.

**This finding must not be overclaimed.** The spread is enormous relative
to the sample: individual held-out folds range from the 23rd–28th
percentile (worse than a coin flip, i.e. some real events rank *below* a
random same-terrain segment under a model that never saw them) up to the
99.9th. With only 25 independent groups, a handful of unlucky/lucky folds
swing the mean substantially — this is a *plausible, worth-investigating*
signal, not a validated, reliable one.

## 8. Feature importance

**Random Forest** (fit on the full dataset for interpretability only — not
one of the LOGO-evaluated models): `elevation_m` 0.270, `slope_deg` 0.227,
`terrain_type_plain` 0.148, `distance_km` 0.126, `terrain_type_mountain`
0.052 — **static terrain/geometry features account for ~82% of total
importance.** Rainfall features together contribute ~14% (`rainy_days_count`
0.043, `monsoon_jun_sep_rainfall_mm` 0.041, `max_daily_rainfall_mm` 0.029,
`annual_rainfall_mm` 0.025). Historical-count features contribute almost
nothing (`nearest_historical_landslide_distance_m_prior` 0.007,
`has_prior_history` 0.007, `historical_landslide_count_prior` 0.006 — ~2%
combined), unsurprising given only 207/32,604 rows (0.6%) have any nonzero
prior history to learn from.

**Logistic Regression** coefficients (standardized units) are **not
trustworthy for interpretation** at this sample size: the largest-magnitude
coefficient is `historical_landslide_count_prior` at **−2.937** — a
negative sign that would nonsensically imply "more prior landslide history
lowers risk." This is a multicollinearity/small-sample artifact (this
feature is 99.4% zero and strongly correlated with `has_prior_history` and
the distance feature), not a real finding, and it is flagged here
explicitly rather than reported as if it were meaningful.
`monsoon_jun_sep_rainfall_mm` (+2.279) and `annual_rainfall_mm` (−1.972)
show the same pattern (two correlated rainfall aggregates with opposite
signs) for the same reason.

**Neither model's importances support a causal claim.** "Elevation and
slope contribute the most importance" describes what the model leaned on
to reproduce the *labels as observed* — which, per §4/§7, are themselves
concentrated in the high-mountain section of the corridor for reasons that
may be genuine hazard, may be survey-location bias, or (most likely) some
mix of both that this data cannot separate. **This study does not, and
cannot, claim that elevation or slope "causes" landslides** — only that
they correlate with where landslides have been documented in this
specific, small, geographically clustered sample.

## 9. Leakage checks

All 7 explicit, executable checks (`app/data/ml/leakage_checks.py`)
**passed**:

| # | Check | Result |
|---|---|---|
| 1 | Future rainfall leakage | PASS — 25 sampled rows' rainfall re-derived independently from each row's own year's NetCDF file; 0 mismatches |
| 2 | Future/lifetime landslide-history leakage | PASS — 0 violations across all 32,604 rows; combined with the 11 unit tests in `test_ml_segment_year_dataset.py` (monotonic non-decreasing prior count; an event's own year excluded from its own row) |
| 3 | Lifetime `historical_landslide_count` leakage | PASS — the ML feature matrix uses only the cutoff-safe `_prior` column, never the raw all-time field |
| 4 | Segment identity leakage | PASS — grouped validation keys on way_id; holding out a way-id removes all its rows with zero segment_id overlap against training |
| 5 | Candidate-pool leakage | PASS — all 2,964 segments are included unconditionally (not pre-filtered to "ever observed" segments, which would itself use future/lifetime information to decide candidacy) |
| 6 | Duplicate records from the same storm | PASS — 17 of 30 event rows have >1 GSI report that segment-year (e.g. one storm, several nearby slide reports); all 30 event rows nonetheless carry exactly `label=1.0`, never inflated |
| 7 | Spatial leakage from nearby/sibling segments | PASS (by the way-id-grouping fix) — 29 positive segment_ids measured to collapse to 25 way-id groups; every grouped split in this study uses way-id, not segment_id |
| 8 | Target-year information used before its prediction point | Addressed by #1/#2 together — the one acknowledged (not hidden) simplification is that rainfall features are contemporaneous with a row's year rather than strictly pre-event, for the 24 of 30 events with only year-precision (see Limitations) |

## 10. ML vs. existing risk engine comparison

| | Full population (misleading alone) | Within-terrain (the honest comparison) |
|---|---|---|
| Existing explainable baseline | 94.8 mean percentile, AUC 0.937 | **58.2 mean percentile, AUC 0.535 (≈ chance)** |
| Logistic Regression (LOGO) | 96.1 mean percentile, AUC 0.954 | **72.9 mean percentile** |
| Random Forest (LOGO) | 97.5 mean percentile, AUC 0.981 | **78.6 mean percentile** |

**Does ML provide useful additional information beyond the current
explainable risk model?** On this evidence: **plausibly yes, within
already-risky terrain classes** — both ML models retain real discriminative
power (73rd/79th percentile) where the existing formula has none (58th,
indistinguishable from chance) once the trivial terrain-class separation is
factored out. This is a genuinely useful, non-obvious finding: it means the
current risk engine's ranking advantage over a naive baseline is coming
almost entirely from its terrain component, and its rainfall/history
components are not currently adding measurable discriminative value at
this sample size — an ML model *might* be extracting more from rainfall
and/or road-class interactions than the current fixed formula does, but see
§7 for why this must be read as "worth pursuing with more data," not "ready
to deploy."

## 11. Limitations

1. **25 independent positive groups is a very small sample** for any
   validation claim. Every headline number in this report (pooled AUC,
   mean percentile) is an average over a handful of independent
   observations and should be read with correspondingly wide, unquantified
   uncertainty — no confidence interval is reported because one would
   overstate precision this sample cannot support.
2. **Reporting/observation-location bias is unresolved.** Nothing in this
   study can distinguish "the high-mountain section near Tawang genuinely
   has more landslides" from "the high-mountain section near Tawang is
   more frequently surveyed/reported." Both the baseline and both ML
   models could be partly or largely learning the latter.
3. **Rainfall is contemporaneous with the label year, not strictly
   pre-event**, for 24 of the 30 event rows (only the 2016 events have a
   day-precise date — see Part 14 inspection). A year's rainfall total
   includes days that came *after* a year-only-dated event, which this
   study cannot separate out. This is a real, acknowledged limitation, not
   hidden in the feature description.
4. **0.25° rainfall grid resolution** (~25–28km cells) is far coarser than
   road-segment spacing — many segments share an identical rainfall
   reading on a given day, capping how much segment-level discrimination
   rainfall alone can ever provide.
5. **`non_event_documented` is empty.** Every comparison in this study is
   against `unobserved` rows, explicitly labeled as such throughout — never
   read as "these are confirmed safe."
6. **LogReg coefficients are not reliably interpretable** at this sample
   size/collinearity level (§8) — Random Forest's impurity importances are
   the more trustworthy of the two, and even those reflect correlation,
   not causation.
7. **Spatial hotspot/kernel-density modeling (framing E) was not
   attempted** — it would more explicitly separate genuine spatial
   clustering from the terrain-confound this study found, and is a
   reasonable next step if more labeled data doesn't materialize.
8. **APSAC hazard-zonation data remains entirely unavailable** (0/2,964
   segments) — unchanged since Part 11.

## 12. Final recommendation

# ML PROTOTYPE POSSIBLE BUT NOT RELIABLE FOR PRODUCTION

A defensible, leakage-checked, grouped-validation ranking prototype **can**
be and **was** built, and it surfaces a real, interesting finding (ML
retains discriminative power within terrain classes where the existing
formula has none). That clears the bar above "insufficient data for
supervised ML" — this is not a case where the exercise was meaningless.

It does **not** clear the bar for production use: 25 independent positive
groups, unresolved reporting-location bias, only 2 distinct event years,
and a headline metric (pooled AUC ~0.95–0.98) that is mostly a restatement
of "recognize mountain terrain" rather than validated fine-grained risk
prediction. Deploying either model today would risk exactly the failure
mode this task warned against: an impressive-looking number built on a
confound, presented with more confidence than 30 labels can support.

**Recommended next steps** (for a future decision, not undertaken here):
obtain additional historical event data with structured event dates
(closing the day-precision gap that limits this to 2 usable years);
obtain or construct a genuine negative/exposure signal (e.g. documented
inspection/monitoring records, if any exist, to populate
`non_event_documented` for real); consider a spatial hotspot/kernel-density
formulation (framing E) that models geographic clustering explicitly rather
than absorbing it into `elevation_m`; and continue using the existing
explainable risk engine, unmodified, as the production system in the
meantime — it is not shown here to be worse than the small-sample ML
prototype, and it remains fully auditable.

---

**Test suite:** 619/619 passing (607 existing + 12 new tests in
`tests/test_ml_feasibility_study.py`, covering `feature_matrix.py`,
`ranking_evaluation.py`, `logo_evaluation.py`, `baseline_risk_scorer.py`,
and `leakage_checks.py`). No production test was modified. No model,
score, or code from this study is imported by `app/core/`, `app/api/`, or
`app/simulation/` (enforced by an AST-based isolation test in
`test_ml_segment_year_dataset.py`).
