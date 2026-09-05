# Part 15A — ML Feature Parity Resolution

**Status:** Analysis + a new, separately-versioned research artifact set.
**No production integration.** `risk_engine.py`, `routing_engine.py`,
`reroute_service.py`, `hazard_state.py`, `app/config.py`, every API route,
and the frontend are all untouched. See Section 9 for the confirming test
run (627/627, unchanged).

This part answers Part 15's open question: given that 4 of the 21 trained
features cannot be honestly computed in production, how do we reach exact
feature parity between training and inference **without fabricating a
value and without silently changing what the model means**?

---

## 1. All 21 features — exact definitions, production availability, leakage analysis

Source of exact definitions: `feature_matrix.py` (encoding),
`build_segment_year_dataset.py` (how each column is computed),
`rainfall_archive_loader.py` (rainfall aggregation), `leakage_checks.py`
(the 7 executed leakage checks), and `models/network.py` (`RoadSegment`).

| # | Feature | Training definition | Production availability | Leakage risk | Action |
|---|---|---|---|---|---|
| 1 | `distance_km` | OSM segment length, real, static | `RoadSegment.distance_km` — identical field | None — pure geometry, no temporal dimension | **Keep** |
| 2 | `slope_deg` | Mean absolute DEM gradient magnitude along segment geometry (Part 4.8), static | `RoadSegment.slope_deg` (nullable — None when DEM coverage was insufficient) | None — static terrain measurement, computed once, never depends on the prediction date | **Keep**, adapter must handle `None` (never silently coerce to 0) |
| 3 | `elevation_m` | Mean DEM elevation sample along segment, static | `RoadSegment.elevation_m` (nullable) | None | **Keep**, same `None`-handling requirement |
| 4 | `historical_landslide_count_prior` | Count of GSI-matched records with resolved `year < row.year`, strict cutoff — verified by `check_historical_count_prior_cutoff` and `check_lifetime_count_not_used_as_a_feature` (both pass) | `RoadSegment.historical_landslide_count` is a **lifetime** (all-time, no year filter) count — not literally the same column | **Low, but non-zero — must be asserted, not assumed.** Using the lifetime count as "count prior to today" is correct *only* while today's date is after every training event year (2015–2025) and the GSI inventory isn't silently updated with new unreviewed events. This is an operational assumption, not a code guarantee. | **Keep as a documented proxy**, gated behind an explicit `as_of_date > max(training event years)` assertion at inference time (see Part 15's Section 5) |
| 5 | `nearest_historical_landslide_distance_m_prior` | Distance (m) to the nearest prior-cutoff GSI match; `NaN` if none | `RoadSegment.nearest_landslide_distance_m` (lifetime, nullable) | Same caveat as #4 | **Keep as proxy**, same assertion requirement |
| 6 | `has_prior_history` | Derived: `historical_landslide_count_prior > 0` | Fully derivable from #4 | None (pure derivation) | **Keep**, recompute from #4/#5 |
| 7 | `annual_rainfall_mm` | Sum of valid (non `-999`) daily rainfall at the segment's nearest 0.25° IMD grid cell, **for the row's own calendar year** — a full-year hindsight total | **Not available.** `rainfall_archive_loader.py` is explicitly offline/build-time only; no code path in `app/core`/`app/api`/`app/simulation` computes a year-total rainfall figure; the live `rainfall_loader.py` holds a single pre-extracted year's (2023) **daily point** CSV used only for the rule engine's `weather_factor`, not a year aggregate | Fabricating any substitute here (last-known year, mean, 0) would create exactly the train/inference distribution mismatch Part 15A's Section 6 rule prohibits | **Drop** (Section 5 decision) |
| 8 | `monsoon_jun_sep_rainfall_mm` | Sum of valid rainfall restricted to calendar months Jun–Sep, same year/cell as #7 | Same gap as #7 (also a full-year-scoped, hindsight-only window — "this year's monsoon" isn't knowable until the monsoon has passed) | Same | **Drop** |
| 9 | `max_daily_rainfall_mm` | Maximum single-day rainfall value across the full year, same cell | Same gap — this is the year's single wettest day, only knowable in hindsight (also already used as a *proxy* input to the rule-based baseline in `baseline_risk_scorer.py`, but only there, as a documented one-off approximation for a *retrospective* evaluation row, not as a live production computation) | Same | **Drop** |
| 10 | `rainy_days_count` | Count of days with rainfall > 1.0mm across the full year, same cell | Same gap | Same | **Drop** |
| 11–18 | `road_type_trunk` / `_trunk_link` / `_primary` / `_primary_link` / `_secondary` / `_secondary_link` / `_tertiary` / `_tertiary_link` | One-hot of OSM `highway` tag, static | `RoadSegment.road_type` (`RoadType` enum) — **the 8 enum values are an exact, verified match to the 8 trained one-hot columns** (checked directly against `models/network.py`) | None — no unseen-category risk exists today | **Keep**, all 8 |
| 19–21 | `terrain_type_hill` / `_mountain` / `_plain` | One-hot of terrain classification, static | `RoadSegment.terrain_type` (`TerrainType` enum) — exact 3-value match | None | **Keep**, all 3 |

**Net result: 17 of 21 features keep, unmodified, with one documented
operational caveat (#4/#5); 4 of 21 (all rainfall aggregates, #7–#10) must
be dropped** — none of them can be computed honestly at inference time,
and no live rainfall feed exists in this project to compute a real-time
substitute either (Section 3).

---

## 2. Option A — make the 4 rainfall features honestly computable as-is

The task is explicit: don't assume annual rainfall is the right shape for
a real-time feature — check what each one actually *is* first.

| Feature | Is it a real-time-friendly quantity? | Why / why not |
|---|---|---|
| `annual_rainfall_mm` | **No.** A sum over an entire calendar year. | For the *current, in-progress* year, this total does not exist yet by definition — it can only be computed once the year is over, which is always in the past relative to "now." For a *past* year it's a look-back, historical constant, not something inference needs a live feed for — but "today's operational annual rainfall" is not a coherent real-time quantity. |
| `monsoon_jun_sep_rainfall_mm` | **No**, for the same reason, with a narrower window (Jun–Sep). Only fully known after September of the target year. Mid-monsoon, it's a partial sum that means something different from the training-time full-window sum. | Same hindsight problem, on a shorter but still multi-month horizon. |
| `max_daily_rainfall_mm` | **No.** The year's single wettest day, only determinable once the year has been observed in full. | Same. |
| `rainy_days_count` | **No.** A full-year count. | Same. |

**Conclusion for Option A: all four training features are, by
construction, full-calendar-year (or full-monsoon-window) hindsight
aggregates — none of the four is a same-day/near-real-time quantity to
begin with.** This isn't a data-pipeline gap that better plumbing could
close; it's that the *feature itself* asks a question ("how much did it
rain this whole year") that cannot be answered before the year is over.
Even with a perfect, always-current live rainfall feed, computing
"`annual_rainfall_mm` for the year containing today's date" at inference
time would still require guessing the rest of the year — which the task
explicitly prohibits ("Do NOT use future rainfall").

**Additionally, independent of the above:** no live/current-updating daily
rainfall feed exists in this project at all. `rainfall_loader.py`'s
`RainfallLoader` reads one static, already-extracted year (2023) from a
committed CSV; `rainfall_archive_loader.py`'s NetCDF archive (2015–2025)
is a fixed historical download, not a feed that gains a new day's
observation as time passes. So even the parts of Option A that *would* be
real-time-shaped (e.g., "how much has it rained so far this year, as a
running partial sum") have no live data source to read from in production
today, on top of the definitional problem above.

**Option A is not viable as specified** — not because of missing
engineering, but because the features themselves are defined as hindsight
quantities, and no live data source exists to feed even a partial-year
version of them.

---

## 3. Option B — redefine rainfall around information available at prediction time

The task suggests candidates: trailing 1-day / 3-day / 7-day accumulated
rainfall, recent maximum daily rainfall, recent rainfall anomaly. These
*are* real-time-shaped quantities in principle (a trailing window ending
"yesterday" is knowable today, unlike a full-year total). Investigated on
two axes: scientific fit, and actual data availability.

**Scientific fit:** A trailing 1–7 day antecedent-rainfall window is a
legitimate landslide-risk covariate in the hazard literature — short-term
saturation is a real, physically distinct mechanism from a year's total
climate character, and is arguably *more* mechanistically relevant to
"is this segment at elevated risk right now" than an annual total is. This
part of Option B is scientifically defensible and would represent a
better real-time feature *design*, not merely a workaround.

**Data availability — this is where Option B fails today:**

- A trailing-window feature ("rainfall over the last 7 days") requires a
  data source that is updated daily, indefinitely, going forward — i.e. a
  **live** rainfall feed.
- This project has no such feed. It has: (a) `rainfall_archive_loader.py`'s
  fixed 2015–2025 NetCDF archive (useful for computing a trailing window
  for a *historical* date that falls inside 2015–2025, but static and
  never gains a 2026 file on its own), and (b) `rainfall_loader.py`'s
  single pre-extracted 2023 corridor CSV (same problem, narrower).
- Building a genuinely live trailing-window feature would mean standing up
  a new, ongoing IMD (or equivalent) daily ingestion pipeline — a
  materially larger, separate engineering effort than anything in scope
  here, and outside what Part 15/15A were asked to design or build.

**Conclusion for Option B: scientifically reasonable, but not adoptable
today** — the blocker isn't the feature *definition*, it's the complete
absence of a live rainfall data source to compute it from. Adopting it
would also, per the task's own instruction, require retraining and
re-evaluating against a **redefined** rainfall feature — which cannot be
done honestly right now either, because the historical archive has no
"yesterday relative to an arbitrary future prediction date" — it only has
complete past calendar years, so even a retrospective trailing-window
*training* feature could only be built for dates that already have
day-resolution data on file (which the archive does have, in principle —
this is a possible **future** research direction, explicitly not pursued
here since it would mean building a new dataset, contradicting the "do
not collect more data" boundary from Part 15). This is left as a
documented, viable-in-the-future avenue, not implemented now.

---

## 4. Option C — remove the 4 rainfall features, retrain 17-feature models (empirically investigated)

Reproduced the **exact** Part 14.3/14.4 methodology, unchanged:

- Same dataset (`segment_year_dataset.csv`, same SHA-256 as v1's own
  `dataset_metadata.json` reference — the file was not touched).
- Same labels, same `y_pseudo`/`is_event` construction, same `way_id`
  grouping (`feature_matrix.py`'s `_way_id()`, reused unmodified).
- Same model factories, same hyperparameters, **no tuning**
  (`models.py::make_random_forest`/`make_logistic_regression`, imported,
  not copied or edited).
- Same LOGO protocol (`logo_evaluation.py::leave_one_group_out`, reused
  unmodified — 25 folds, one per independent positive way-group).
- Same ranking metrics (`ranking_evaluation.py`, unmodified).
- The **only** change: the feature matrix drops the 4 rainfall columns
  (new module `feature_matrix_v2_17feature.py`, built by importing —
  never duplicating — `feature_matrix.py`'s `CATEGORICAL_COLUMNS` and
  missing-value sentinel).

Real results (run in this session; full detail in
`artifacts/v2_17_feature/validation_metadata.json`):

| Model | Within-terrain mean percentile | Median | Min | Max | Pooled AUC |
|---|---|---|---|---|---|
| Rule-based baseline (unchanged, for reference) | 58.2 | — | — | — | 0.535 |
| **v1, 21-feature** Logistic Regression | 72.94 | 75.87 | 27.86 | 99.87 | 0.9540 |
| **v1, 21-feature** Random Forest | 78.63 | 81.16 | 23.37 | 99.94 | 0.9811 |
| **v2, 17-feature (no rainfall)** Logistic Regression | **74.92** | 77.14 | 29.29 | 99.94 | 0.9649 |
| **v2, 17-feature (no rainfall)** Random Forest | **75.59** | 86.30 | 25.38 | 99.94 | 0.9755 |

Observations:

- **Random Forest loses 3.04 within-terrain percentile points** without
  rainfall (78.63 → 75.59) — a real but modest cost. Its pooled AUC drops
  slightly (0.9811 → 0.9755).
- **Logistic Regression actually improves slightly without rainfall**
  (72.94 → 74.92, pooled AUC 0.9540 → 0.9649) — rainfall was not a
  net-positive contributor for this model at this sample size; consistent
  with `feature_importance.json`'s existing caveat that the 21-feature
  LR's coefficients are "unstable/multicollinear at this sample size."
- **Both 17-feature models remain enormously ahead of the rule-based
  baseline** (75.6 / 74.9 vs. 58.2 mean within-terrain percentile — the
  baseline is essentially at chance, per Part 14's own finding). The
  central Part 14 conclusion — "ML retains meaningfully more within-terrain
  discriminative power than the existing explainable baseline" — **holds
  identically without rainfall.**
- Feature importance reshuffles sensibly: with rainfall gone, `elevation_m`
  (27.0% → 33.2%) and `slope_deg` (22.7% → 25.3%) absorb most of the
  released importance in the Random Forest — no feature becomes newly
  dominant in a way that looks like an artifact; the ranking still leads
  with the same physically-sensible terrain features it always did.
- No leakage rule was touched: same candidate pool (2,964 segments), same
  `way_id` grouping, same prior-cutoff historical features, same labels.
  This is purely a column-removal exercise re-run through unmodified
  validation code.

**Option C is empirically defensible**: it recovers exact train/inference
feature parity, costs a small, honestly-measured amount of Random Forest
ranking quality (and nothing for Logistic Regression), and preserves the
core finding that motivated considering ML at all.

---

## 5. Decision

**Recommendation: Option C — remove the 4 unavailable rainfall features
and adopt the 17-feature model as the candidate for any future
integration, versioned separately from v1.**

Reasoning, weighing all four options directly against each other:

- **Option A is not viable** (Section 2) — the features are hindsight
  quantities by construction; no amount of production engineering makes
  "this year's total rainfall" computable before the year ends, and no
  live feed exists to compute even a partial version.
- **Option B is the scientifically nicer idea but not implementable
  today** (Section 3) without first building a live rainfall ingestion
  pipeline — a materially larger project explicitly out of scope for Part
  15/15A, and doing it would still require new data collection and
  retraining against a feature definition that has never been validated,
  which Part 15's boundaries also rule out for this part.
- **Option D (don't integrate yet) would be the right call if dropping
  rainfall destroyed the model's value** — it does not. The measured cost
  is small (RF: −3.0 points) or negative (LR: improves), and the
  post-drop numbers still show the same qualitative result Part 14
  reported: **both models discriminate meaningfully better than the
  rule-based baseline within terrain class**, which is the entire premise
  under which ML was worth investigating in the first place. Choosing D
  here would be over-cautious given the actual, measured evidence, and
  would leave the door closed on a signal that still clears the bar Part
  14 set for "not reliable for production, but worth continuing to
  evaluate."
- **Option C directly satisfies the "no fabrication, no silent meaning
  change" goal** stated at the top of this task: nothing is invented for
  the missing features (`None`, 0, mean, stale-year rainfall are all
  explicitly rejected — Section 6), and the model's *meaning* changes
  transparently (a new, separately versioned, separately validated
  artifact — not a quiet substitution under the same name).

This decision does **not** by itself declare the 17-feature model
production-ready. It resolves the *feature parity* blocker specifically.
The MODEL_CARD.md limitations that already applied to v1 — 25 independent
positive groups, zero confirmed negatives, no calibration, unresolved
reporting-location bias — apply identically to v2 and are unaffected by
this decision. Whether/how an advisory ML signal gets wired into the
application at all remains governed by Part 15's architecture design
(Option C there: advisory only, config-gated, `ML_RISK_ENABLED=false`
default) — this part only decides *which* model that architecture would
eventually load, if and when that's approved.

---

## 6. Modeling rule — honored, not bypassed

No placeholder value was substituted for any of the 4 dropped rainfall
features anywhere in this investigation — not `None`, not `0`, not a
mean, not a stale/latest-available year's annual figure. The 17-feature
model's feature vector genuinely has no rainfall slot at all; there is
nothing for a production caller to "fill in" incorrectly, which is the
entire point of Option C over any imputation-based alternative. This also
means the v2 model has **zero possible train/inference distribution
mismatch on rainfall**, by construction — not "unlikely," but structurally
impossible, since the column doesn't exist in either the training matrix
or any future inference matrix built via `feature_matrix_v2_17feature.py`.

---

## 7. Proposed feature set (if/when retraining is approved for real integration)

Exactly the 17 features validated in Section 4, in this order (from the
new `artifacts/v2_17_feature/feature_schema.json`, produced by actually
running the training/evaluation code — not hand-typed):

```
distance_km, slope_deg, elevation_m,
historical_landslide_count_prior, nearest_historical_landslide_distance_m_prior, has_prior_history,
road_type_primary, road_type_primary_link, road_type_secondary, road_type_secondary_link,
road_type_tertiary, road_type_tertiary_link, road_type_trunk, road_type_trunk_link,
terrain_type_hill, terrain_type_mountain, terrain_type_plain
```

No hyperparameter changes from v1 (`make_random_forest()`/
`make_logistic_regression()`, unmodified — confirmed identical in
`artifacts/v2_17_feature/model_config.json`).

---

## 8. Artifact/version changes made in this part

Per the task's explicit instruction not to overwrite the existing Part
14.4 artifacts:

```
backend/app/data/ml/
    artifacts/                          <- UNCHANGED (v1, 21-feature, Part 14.4)
        random_forest_model.joblib
        logistic_regression_model.joblib
        logistic_regression_scaler.joblib
        feature_schema.json
        model_config.json
        validation_metadata.json
        feature_importance.json
        dataset_metadata.json
        model_manifest.json
        MODEL_CARD.md
        v2_17_feature/                  <- NEW (v2, 17-feature, Part 15A)
            random_forest_model.joblib
            logistic_regression_model.joblib
            logistic_regression_scaler.joblib
            feature_schema.json
            model_config.json
            validation_metadata.json
            feature_importance.json
            dataset_metadata.json
            model_manifest.json
            MODEL_CARD.md
    feature_matrix.py                   <- UNCHANGED (v1's builder)
    feature_matrix_v2_17feature.py      <- NEW (v2's builder, imports from
                                            feature_matrix.py, duplicates nothing)
    save_model_artifacts.py             <- UNCHANGED (v1's save script)
    save_model_artifacts_v2_17feature.py <- NEW (v2's save script, mirrors
                                            v1's script structure exactly)
```

**Deliberate deviation from the task's literal `v1_21_feature/` /
`v2_17_feature/` example:** v1's artifacts remain at their original flat
path (`artifacts/`) rather than being moved into `artifacts/v1_21_feature/`.
Moving them would mean editing every existing reference to those exact
paths (`MODEL_CARD.md`, `model_manifest.json`'s self-description, the Part
14 reports, `ml_integration_design_part15.md`) purely for cosmetic
symmetry, with real risk of silently breaking one. Leaving v1 exactly
where it is satisfies "do not overwrite the existing Part 14.4 artifacts"
and "preserve the existing model for reproducibility" literally and
safely; v1 is v1 by being first and unmoved, and `v2_17_feature/`'s own
`model_manifest.json` records the relationship explicitly
(`version_relationship` key) so the lineage is discoverable without
requiring the path to say so.

Neither new script nor artifact set is imported by `app/core`, `app/api`,
`app/simulation`, or any test outside the ones that already exercise the
ML package's existing conventions — this remains research code, exactly
like v1 was on its own delivery.

---

## 9. What should happen next

1. **This decision (Option C, 17-feature model) should be reviewed and
   explicitly approved** before any integration work begins — this
   document is analysis, not an approval to proceed.
2. If approved, the **existing Part 15 architecture design already
   describes the correct integration shape** (advisory signal,
   `ML_RISK_ENABLED=false` default, additive-only) — Part 15A does not
   change that recommendation, only which model artifact it would
   eventually point at (`artifacts/v2_17_feature/` instead of the flat
   `artifacts/`).
3. The still-open items from Part 15's own blocker list remain open and
   are unaffected by this part: no artifact hash check yet, no
   per-instance explanation method, the lifetime-vs-prior-year count
   caveat (#4/#5 above) should be turned into an explicit runtime
   assertion when an inference adapter is actually built, and the
   unresolved GSI reporting-location bias is inherited unchanged.
4. **Option B (real-time-shaped rainfall features) is worth revisiting
   as a separate, future, explicitly-scoped effort** if a live rainfall
   feed is ever built for this project — it is the scientifically
   stronger direction, just not an available one today.
5. No code path in `app/core`, `app/api`, or `app/simulation` should
   import anything from this part until that separate integration
   approval happens.

---

## 10. Test suite confirmation

Full backend test suite run at the end of this part, after all files
above were created:

```
627 passed, 12 warnings in ~157s
```

Matching the expected, unchanged baseline. No test was added, removed, or
modified; no production file was touched.
