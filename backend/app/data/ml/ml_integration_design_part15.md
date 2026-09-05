# Part 15 — ML Risk Integration Design (DESIGN ONLY — nothing implemented)

**Status:** Design document only. No production code was modified. See
Section 15 for the confirming test run (627/627 passing, unchanged).

---

## 1. Current architecture (as inspected)

Files read in full: `core/risk_engine.py`, `core/routing_engine.py`,
`core/reroute_service.py`, `core/hazard_state.py`, `core/weather_factor.py`,
`store/state_store.py`, `api/routes_routing.py`, `models/network.py`
(`RoadSegment`, `RoadType`, `TerrainType`), `models/risk.py`, relevant slices
of `app/config.py`, `data/rainfall_loader.py`, and the frontend
`RiskBreakdown.jsx`.

```
OSM (roads) + DEM (slope/elevation) + GSI (historical landslides,          STATIC,
matched <=500m) + hazard-zonation layer (optional)                        loaded once
        |                                                                 at startup
        v                                                                 into RoadSegment
 RoadSegment (per-segment fields: slope_deg, elevation_m,
   historical_landslide_count, nearest_landslide_distance_m,
   landslide_hazard_score, road_type, terrain_type, ...)
        |
        |   + optional CURRENT-CONTEXT inputs (weather_factor,             DYNAMIC,
        |     incident_factor) from:                                       per-request
        |       - manual API params (routes_routing.py)
        |       - simulated HazardEvents -> hazard_state.py's
        |         SegmentHazardContext (Part 8)
        |       - real-but-single-year rainfall -> weather_factor.py
        |         (Part 10, uses data/rainfall_loader.py, 2023 corridor CSV)
        v
 risk_engine.assess_segment_risk()                                  <- EXPLAINABLE
   risk_score = clamp(                                                 RULE-BASED,
       TERRAIN_WEIGHT*slope_risk + HISTORICAL_WEIGHT*historical_risk    NOT trained
     + WEATHER_WEIGHT*weather_risk + INCIDENT_WEIGHT*incident_risk )
   -> RiskResult{risk_score, risk_level, breakdown, reasons[], ...}
        |
        v
 routing_engine.build_risk_aware_graph() / risk_aware_edge_cost()
   - edges with risk_score >= HARD_UNSAFE_RISK_THRESHOLD (0.65) or
     closed=True are EXCLUDED from the graph entirely
   - surviving edges cost travel_time_min * (1 + RISK_WEIGHT * risk_score)
   - compute_route_risk_profile(): aggregate = 0.7*max + 0.3*mean segment risk
        |
        v
 compare_fastest_and_safe_routes() -> CASE A/B/C (fastest-is-safe /
   safer-route-selected / no-safe-route-available)
        |
        v
 reroute_service.evaluate_route_decision()                          <- thin
   - hysteresis (0.05) against previous_route, safety always overrides it     decision
   -> CONTINUE / REROUTE / SUSPEND                                            layer only
        |
        v
 api/routes_routing.py  ->  frontend (RouteSummary, RiskBreakdown.jsx,
   RouteComparison, HazardControl, SegmentDetailPanel)
```

Key invariants this system currently guarantees, that any ML addition must
preserve:

1. **Every risk number is explainable.** `RiskResult` always carries an
   unweighted per-component `breakdown` and a plain-language `reasons` list.
   Nothing is a black box today.
2. **`risk_score` is never called a probability**, anywhere — the module
   docstrings and `RiskResult.methodology_note` say so explicitly.
3. **Hard safety behavior (exclusion, closure, SUSPEND) is driven by
   explicit rules and explicit hazard state**, not by a fitted model.
4. **`risk_engine.py` has zero routing knowledge** and `routing_engine.py`
   has zero ML knowledge today — the dependency graph is one-directional
   (`routing_engine -> risk_engine`, `reroute_service -> routing_engine`).
5. Weights/thresholds (`TERRAIN_WEIGHT`, `HARD_UNSAFE_RISK_THRESHOLD`,
   `ROUTE_CHANGE_HYSTERESIS_SCORE`, ...) in `app/config.py` are each
   individually justified in comments and have been explicitly protected
   from casual recalibration in past parts (see `risk_engine.py`'s
   `FLOOD_HAZARD_NOTE`, which documents *refusing* to add a new weight for
   exactly this reason).

---

## 2. ML artifact architecture (as inspected)

- **Model:** `RandomForestClassifier` (300 trees, `max_depth=5`,
  `class_weight="balanced_subsample"`, `random_state=42`) and a Logistic
  Regression pipeline, both saved under `backend/app/data/ml/artifacts/`.
- **Training data:** `segment_year_dataset.csv`, 32,604 rows = 2,964 real
  road segments x 11 years (2015-2025). 21 input features after one-hot
  encoding (`feature_schema.json`).
- **Labels:** 30 `event` rows (real GSI-matched landslide, in its own
  event year), **0 confirmed-negative rows**, 32,574 `unobserved` rows
  (treated as pseudo-negative *for model fitting only* — never as ground
  truth for any reported metric).
- **Validation:** Leave-one-way-group-out (25 independent physical roads).
  Reported generalization estimate: **mean within-terrain percentile 78.6
  (RF) / 72.9 (LR)**, vs. the existing rule-based engine's ~0.535-ish
  within-terrain ranking quality (baseline comparison in
  `validation_metadata.json` reports a 94.8 *full-population* mean
  percentile for the baseline, which — per the Part 14 report — is
  itself inflated by the same mountain/hill clustering effect the
  within-terrain metric exists to correct for; the two numbers are not
  directly comparable at face value, which is exactly why within-terrain
  is the metric this design treats as authoritative for both).
- **Explicit "not intended for" list in `MODEL_CARD.md`:** autonomous
  safety decisions, claiming calibrated probabilities, replacing domain
  experts, production deployment without further validation.
- **Explicit conclusion, twice (Part 14.3 and 14.4):** "ML PROTOTYPE
  POSSIBLE BUT NOT RELIABLE FOR PRODUCTION."
- **Current integration status:** none. `model_manifest.json` states
  plainly: "NOT integrated. Not imported by `app/core`, `app/api`, or
  `app/simulation`." Confirmed by inspection — no import of
  `app/data/ml` exists anywhere under `app/core`, `app/api`, or
  `app/simulation`.

This is a **ranking** prototype, not a calibrated classifier, and it was
built and evaluated as one throughout (percentile-rank metrics, never
accuracy/precision/recall/F1). Any production use must preserve that
framing.

---

## 3. Recommended integration approach

### Evaluated options

**A. ML completely replaces existing risk score — REJECTED.**
Would make routing safety depend on a model with 25 independent positive
examples, zero confirmed negatives, and an explicit model-card statement
that it is not intended for autonomous safety decisions. It would also
destroy explainability: `RiskBreakdown`'s four named components would be
replaced by a single opaque number. There is no calibration evidence for
what a given output value "means," so even routing thresholds like
`HARD_UNSAFE_RISK_THRESHOLD` would become uninterpretable if computed from
this signal alone.

**B. ML becomes an additional weighted component inside the existing
formula (a 5th term, e.g. `ML_WEIGHT * ml_risk`) — REJECTED for now.**
Two problems. First, adding a weight while holding the sum at 1.0 means
*shrinking* `TERRAIN_WEIGHT`/`HISTORICAL_WEIGHT`/`WEATHER_WEIGHT`/
`INCIDENT_WEIGHT` — exactly the "quietly recalibrating a formula this
project has repeatedly been told not to casually change" problem the
`risk_engine.py` docstring already called out for flood hazard in Part 11,
now for a source with far weaker evidence behind it than an official
flood-zonation layer would have. Second, folding an uncalibrated,
25-positive-group model score into the same scalar as the rule-based
components would silently launder its uncertainty into a number that today
carries an implicit "every input here is auditable" guarantee. This
remains a legitimate *future* option once (a) calibration is demonstrated,
(b) confirmed negatives exist, and (c) a weight-rebalancing decision is
made deliberately and separately — not as a side effect of "turning ML on."

**C. ML acts as an advisory signal while existing risk remains
authoritative — RECOMMENDED.** Preserves every invariant in Section 1:
`risk_score`/`risk_level`/thresholds/hysteresis are computed exactly as
today, unchanged bit-for-bit; the ML signal is surfaced *alongside* them
as its own separately-labeled value, with its own explicit "not a
probability, not validated for this decision" framing. This is also the
only option directly consistent with the model card's own stated
intended use ("prototype road-segment risk *ranking* ... for research and
further evaluation").

**D. ML modifies routing cost without modifying displayed risk —
PARTIALLY ADOPTED, narrowly.** Silently nudging routing cost by a signal
the UI never shows would violate the explainability principle this whole
system is built on (a route change with no visible reason). However, a
*narrow, disclosed, opt-in* form of this is defensible as an extension of
C: use the ML signal only to break ties among routes that the *existing*
rule-based formula already considers close enough that
`ROUTE_CHANGE_HYSTERESIS_SCORE` would otherwise pick between them
arbitrarily/by path-order — and only when this is explicitly surfaced in
`RouteDecision.reason`/`RiskAwareRouteResult.reasons` as "ML signal used
as tie-breaker." This must be its own separate config flag
(`ML_TIE_BREAK_ENABLED`, default `false`), independent of and layered on
top of `ML_RISK_ENABLED` — never a blanket cost modifier.

**E. Other —** not needed; C (default) plus the narrow, optional,
disclosed extension of D described above covers the space without
inventing a new mechanism.

### Recommendation

**Adopt C as the architecture. Treat D's tie-break variant as an optional,
separately-flagged, later-stage extension — not part of the initial
integration.** The existing explainable rule-based engine remains the
sole authority for `risk_score`, `risk_level`, routing cost, the hard
unsafe threshold, and CONTINUE/REROUTE/SUSPEND. The ML model contributes
a clearly-labeled, independently-displayed, independently-toggleable
`ml_risk_signal` that a human (or, later and separately-approved, a
narrowly-scoped tie-breaker) can consult — never one that can independently
close a segment, force a reroute, or suspend dispatch.

This is not just "the safe default" — it is the *only* option consistent
with what the model card itself says the model is for, given the honest
state of the evidence (25 independent positives, 0 confirmed negatives,
no calibration). Revisiting B is reasonable once that evidence picture
changes; it should not be adopted preemptively.

---

## 4. Data flow (recommended)

```
                         ML_RISK_ENABLED = false  (default)
                                    |
                     +--------------+--------------+
                     |                              |
                    OFF                             ON
                     |                              |
                     v                              v
     existing pipeline, BYTE-FOR-BYTE      existing pipeline runs EXACTLY
     UNCHANGED (Section 1 diagram)         as in the OFF case, UNCHANGED,
                                            AND, in parallel, a new adapter
                                            attempts an ML signal:

                                     RoadSegment (static features)
                                       + resolved operational date
                                       + (if available) rainfall
                                         climatology reference
                                                |
                                                v
                                     ml/inference_service.py
                                       - loads artifacts once (versioned)
                                       - builds a 21-column feature row
                                         via the SAME encoding as
                                         feature_matrix.py
                                       - try: model.predict_proba(row)
                                         except -> MLUnavailable
                                                |
                                     +----------+-----------+
                                     |                       |
                              success (float in [0,1])   any failure/
                                     |                     missing input
                                     v                       |
                          MLRiskSignal{                      v
                            value,                    MLRiskSignal = None
                            model_version,             (logged, counted,
                            terrain_relative_          NEVER raised up)
                              percentile,
                            feature_coverage_ok,
                            caveats[] }
                                     |
                                     v
                     attached ALONGSIDE (never inside) the existing
                     RiskResult / RouteRiskProfile / RouteDecision,
                     e.g. as an optional sibling field the API response
                     model adds (additive schema change only)
                                     |
                                     v
                     frontend shows a SEPARATE "ML Risk Signal (advisory,
                     experimental)" block, never merged into the existing
                     risk bar/number
```

`risk_engine.py`, `routing_engine.py`, `reroute_service.py`, and
`hazard_state.py` are not modified by this data flow — they run exactly as
today whether `ML_RISK_ENABLED` is true or false. The new adapter sits
*beside* them, called by a thin new layer (the API route handler, most
likely — see Section 11) that already has access to both the `RiskResult`
and the segment.

---

## 5. Feature availability (production vs. training) — the core finding of this design

| Feature | Training source | Production availability today | Verdict |
|---|---|---|---|
| `distance_km` | OSM | `RoadSegment.distance_km` | **Available** |
| `slope_deg` | DEM (Part 4.8) | `RoadSegment.slope_deg` (nullable) | **Available** (must handle `None`) |
| `elevation_m` | DEM | `RoadSegment.elevation_m` (nullable) | **Available** (must handle `None`) |
| `road_type_*` (8 categories) | OSM `highway` tag | `RoadSegment.road_type` (`RoadType` enum) | **Available, exact match** — `RoadType`'s 8 values (`trunk`, `trunk_link`, `primary`, `primary_link`, `secondary`, `secondary_link`, `tertiary`, `tertiary_link`) are identical to the 8 trained one-hot columns. No unseen-category risk. |
| `terrain_type_*` (3 categories) | derived terrain classification | `RoadSegment.terrain_type` (`TerrainType` enum: `plain`/`hill`/`mountain`) | **Available, exact match** |
| `historical_landslide_count_prior` | GSI count, strict `year < row.year` cutoff (verified by `leakage_checks.py::check_historical_count_prior_cutoff`) | `RoadSegment.historical_landslide_count` — a **lifetime** count with no year dimension at all (loaded once at startup, no per-year filtering exists in production) | **Usable with a documented caveat, not a blocker**: since production inference always targets "now" (2026, after every training year), the lifetime count *is* "count prior to now" **as long as the underlying GSI inventory is never silently updated with new events without a corresponding model/feature refresh**. This equivalence must be asserted explicitly at inference time (Section 9/10), not assumed silently. |
| `nearest_historical_landslide_distance_m_prior` | same prior-cutoff GSI join | `RoadSegment.nearest_landslide_distance_m` (lifetime, nullable) | **Same caveat as above** |
| `has_prior_history` | derived (`count_prior > 0`) | derivable from the field above | **Available** |
| `annual_rainfall_mm` | full-year IMD NetCDF archive aggregate, **for the row's own year** | **Not available in production.** `rainfall_archive_loader.py` is explicitly offline/build-time only ("nothing in app/core, app/api, or app/simulation imports it"); the live pipeline (`data/rainfall_loader.py`) only holds a single pre-extracted year's (2023) **daily point** corridor CSV, used only to compute the rule-engine's `weather_factor`, not a year-level aggregate for an arbitrary "current" year. | **BLOCKER** (see below) |
| `monsoon_jun_sep_rainfall_mm` | same archive, monsoon-window aggregate | same gap | **BLOCKER** |
| `max_daily_rainfall_mm` | same archive, yearly max | same gap | **BLOCKER** |
| `rainy_days_count` | same archive, yearly count | same gap | **BLOCKER** |

**Bottom line:** 17 of the 21 trained features are honestly computable in
production today (all static terrain/road/OSM/GSI-derived features). The
4 rainfall aggregate features — which the model's own `feature_importance.json`
ranks as meaningfully contributing (`rainy_days_count`,
`monsoon_jun_sep_rainfall_mm`, `max_daily_rainfall_mm`,
`annual_rainfall_mm` together account for roughly 14% of RF impurity
importance) — are **not currently obtainable for "the current operational
year" in production**, because:

1. The full multi-year rainfall archive (`data/RF25_ind<year>_rfp25.nc`)
   is explicitly kept out of version control (confirmed: the repository's
   `data/` directory is currently untracked in this working copy — present
   locally right now, but not guaranteed present in every deployment).
2. Even where the archive is present, an **annual** aggregate for the
   *current, in-progress* calendar year cannot be computed honestly until
   the year is over — the training feature is a full calendar-year total,
   which is fundamentally a hindsight quantity for whichever year the row
   describes.

This is the single most important finding of this design pass. Section 10
formalizes it as Blocker #1.

---

## 6. Score semantics — do not call this a probability

`model_config.json` confirms both models are plain, uncalibrated
scikit-learn estimators (`RandomForestClassifier`, no `CalibratedClassifierCV`
wrapper; `LogisticRegression`, standard `lbfgs`). `predict_proba()`'s
class-1 output is bounded in [0,1] by construction, but **boundedness is
not calibration** — nothing here was checked against a reliability diagram
or Brier score, and the model card explicitly disclaims exactly this
reading:

> "A score from either model is not a probability of a landslide occurring."

Design rule: **the raw `predict_proba()` output must never be surfaced,
labeled, or logged as a probability anywhere** — not in API responses, not
in logs, not in the UI, not in code comments describing it. Approved
terms: **"ML risk signal"**, **"ML ranking score"**, **"learned risk
signal"**.

**Normalization for the 0-1 risk framework:** the raw score is already
numerically in [0,1], so no rescaling is needed to *fit* the framework —
but presenting the raw magnitude as meaningful would misrepresent what was
actually validated. The only property this model was shown to have is
**relative ranking quality within a terrain class** (the within-terrain
percentile metric). The design therefore recommends exposing **two**
numbers, both clearly labeled, rather than collapsing to one:

- `ml_raw_score` (float, [0,1]) — the raw `predict_proba()` output, labeled
  "ML ranking score (raw model output, not a probability)".
- `ml_terrain_relative_percentile` (float, [0,100]) — this segment's raw
  score's percentile rank among all segments of the *same*
  `terrain_type`, computed against a fixed reference population (e.g. all
  2,964 segments' current-feature scores, refreshed on artifact
  reload) — this is the quantity LOGO validation actually evaluated, so it
  is the more defensible one to lead with in the UI ("this segment ranks
  higher than X% of same-terrain segments, per an experimental ranking
  model").

Neither number replaces or feeds into `risk_score`/`risk_level` (Section 3
option C).

---

## 7. Temporal feature availability — detailed answers

- **What can be computed in real time?** All static terrain/OSM/GSI-derived
  features (17 of 21 — Section 5's "Available" rows), computed fresh from
  the already-loaded `RoadSegment` at request time. No caching concerns
  beyond what already exists (`StateStore` holds segments in memory).
- **What features are static?** `distance_km`, `slope_deg`, `elevation_m`,
  `road_type_*`, `terrain_type_*` — fixed for the lifetime of a loaded
  network (only change on `StateStore.load()`).
- **What historical data is required?** The GSI-derived count/distance
  pair — already loaded onto `RoadSegment` at startup; no additional
  historical data acquisition needed for those two.
- **What rainfall window should be used?** This requires a decision this
  document does not make unilaterally (flagged for explicit approval,
  per the task's Section 4 instruction not to invent an answer):
  - **Option A (recommended if ML rainfall features are attempted at
    all):** use the most recently *complete* calendar year's archive
    aggregate as a labeled **"reference rainfall climatology"** input,
    explicitly not claimed to be "this year's rainfall." This changes the
    feature's meaning versus training (which used the row's *own* year)
    and must be disclosed as a deviation, not silently substituted.
  - **Option B (recommended as the actual default):** treat the rainfall
    features as **unavailable** whenever the true current-year aggregate
    cannot be honestly computed (which, per Section 5, is always true for
    an in-progress year) — i.e., do not compute an approximate rainfall
    feature at all; let the missing-feature fallback in Section 8 apply.
  This design recommends **Option B as the default fallback behavior**
  and **Option A only as an explicitly-configured, clearly-labeled
  alternative** if a future part decides the approximation is worth
  making — never silently.
- **How should the current operational date be represented?** A single
  `as_of_date` (default: server current date, e.g. via a small
  `datetime.date.today()`/injectable clock for testability — mirroring
  how `weather_factor_for_segment()` already takes an explicit `date`
  parameter rather than reading a global clock) threaded through the
  inference call. This date determines the "year" used for any rainfall
  lookup (if Option A is later enabled) and is asserted (Section 5) to be
  after the training data's last event year, so the lifetime GSI counts
  remain a valid "prior" proxy.
- **What happens if required historical features are unavailable?** ML is
  treated as **unavailable for that request** (Section 8) — never
  approximated with an invented value for a *non-rainfall* feature; the
  two GSI fields are always available today, so this path is currently
  theoretical but must still be handled (e.g. a future segment added
  without a completed GSI join).
- **What happens if the ML artifact cannot produce a valid prediction?**
  Section 8, mandatory fallback.

---

## 8. Failure / fallback behavior (mandatory)

**Governing rule: ML failure must never prevent, delay, or alter the
outcome of route calculation.** Every failure mode below resolves to the
same outcome — `MLRiskSignal = None`, existing explainable engine
continues unchanged, failure is logged/counted but never raised past the
adapter boundary.

| Failure | Detection | Fallback |
|---|---|---|
| Model file missing | `FileNotFoundError` on load | Adapter fails to initialize once at startup; `ML_RISK_ENABLED` is treated as effectively `false` for the process lifetime; logged once, not per-request |
| Model loading failure (corrupt joblib, version mismatch) | exception during `joblib.load()` | Same as above |
| Malformed feature vector (wrong column count/order) | explicit schema check against `feature_schema.json`'s `feature_names_in_order` before calling `.predict_proba()` — never trust column order implicitly | Per-request `MLRiskSignal = None`; this is exactly the failure mode `feature_schema.json`'s own note warns about ("a mismatched column set/order will silently produce wrong scores") — so the adapter must verify, not just hope |
| Missing rainfall (always true today per Section 5/7, Option B default) | rainfall features flagged unavailable before feature-row assembly | Per-request `MLRiskSignal = None` (until/unless Option A is explicitly enabled) |
| Missing historical features | GSI fields absent on a segment (shouldn't happen today, but not asserted impossible) | Per-request `MLRiskSignal = None` |
| Unsupported segment (e.g. a `road_type`/`terrain_type` value outside the trained categories) | encoding step produces an all-zero one-hot for that column group | Per-request `MLRiskSignal = None` — currently unreachable given the exact enum match in Section 5, but must be checked defensively rather than silently producing a row the model was never trained to see |
| Model inference exception (any other) | broad `except Exception` around the single `predict_proba()` call, nothing else | Per-request `MLRiskSignal = None`, logged with exception type (not full input data, to avoid noisy/PII-shaped logs) |
| NaN/invalid ML score | explicit `math.isnan`/range check on the returned float before use | Treated identically to an inference exception — `MLRiskSignal = None` |

Implementation shape: a single adapter entry point, e.g.
`get_ml_risk_signal(segment, as_of_date) -> Optional[MLRiskSignal]`, whose
**every** internal failure path returns `None` rather than raising. Callers
(the future API layer) always do:

```python
ml_signal = get_ml_risk_signal(segment, as_of_date) if ML_RISK_ENABLED else None
# ... existing risk_result computed exactly as today, unconditionally ...
# ml_signal attached as an optional sibling field only if not None
```

No `try/except` is needed at the call site itself, because the adapter
never raises — this keeps the "additive, reversible" property concrete:
deleting the two `ml_signal` lines above fully reverts behavior.

---

## 9. Routing effect

Per Section 3's chosen architecture (C), ML must not independently:

- exclude a segment from the risk-aware graph,
- change `risk_aware_edge_cost()`'s formula,
- change `HARD_UNSAFE_RISK_THRESHOLD` behavior,
- change hysteresis,
- change CONTINUE/REROUTE/SUSPEND classification.

**What it CAN contribute (Stage 1, default-on once `ML_RISK_ENABLED=true`):**
- **Route ranking (display-only):** when returning a `RiskAwareRouteResult`
  or `RouteDecision`, attach an `ml_risk_signal` list/summary per candidate
  route alongside the existing (unchanged) `RouteRiskProfile`, so a human
  operator sees "the rule-based engine says X; the experimental ML ranking
  model separately says Y" as two clearly distinct signals.
- **Decision explanation:** `reasons`/`reason` text can optionally note the
  ML signal's value for context ("Note: an experimental ML ranking signal
  separately flags this segment as high relative to same-terrain segments
  — not used in this decision"), without it having caused anything.

**What requires a SEPARATE, later, explicitly-approved config flag
(Section 3's narrow option D) and is NOT part of the default-on behavior:**
- Using the ML signal as a tie-breaker **only** among routes the existing
  hysteresis logic already treats as materially equivalent — i.e., it may
  decide *which* of two already-safe, already-close routes to prefer, never
  whether a route is safe. This must always be visible in
  `RouteDecision.reason`.

**Hard rule, restated:** a weak prototype model (25 independent positives,
0 confirmed negatives, no calibration) must never gain the power an
official hazard-zonation layer or an active `HazardEvent` has today (both
of which *can* legitimately force exclusion/closure, because they are
either curated official data or explicit operational state — see
`risk_engine.py`'s Part 11 `historical_landslide_risk()` and
`hazard_state.py`'s `HAZARD_CLOSURE_TYPES`). This model qualifies for
neither category yet.

---

## 10. Explainability

**What CAN be shown honestly:**
- The model's **global** feature importance list (`feature_importance.json`,
  already computed and saved) as a static "what this experimental model
  weighs most heavily across all training data" panel — e.g. "Elevation,
  slope, and terrain classification are the largest contributors to this
  model's ranking (impurity-based importance, correlational, not causal)."
  This is honest because it is exactly what was computed and exactly how
  it's caveated in the artifact itself.
- The **raw feature values** that were fed into a given prediction (e.g.
  "this segment: slope 34.2°, elevation 1840m, terrain=mountain") shown
  next to the global importance list, letting a reader connect the two
  themselves — without the system asserting a per-segment causal
  attribution it cannot support.

**What must NOT be fabricated:**
- **Per-feature causal explanations for one specific prediction**
  ("this segment is HIGH because of rainfall" as a specific, decomposed,
  per-instance claim). Random Forest impurity importance and Logistic
  Regression standardized coefficients are **global, model-level**
  statistics — they do not, on their own, decompose one single
  prediction into per-feature contributions. Producing that decomposition
  properly needs SHAP/treeinterpreter/permutation-based per-instance
  methods, all explicitly out of scope for this part ("DO NOT add SHAP or
  other large dependencies yet").
- Any text implying "cause of landslide" — `feature_importance.json`'s own
  caveats are explicit: "Neither model's importances support a causal
  claim about what 'causes' landslides." UI copy must say **"model
  feature contribution"**, never **"cause."**

**Recommended UI framing** (extending, not replacing, the existing
`RiskBreakdown.jsx` panel — as a new, visually distinct, separately-labeled
block):

```
ML Risk Signal (experimental, advisory — not used in routing decisions)
Ranking score: 0.71   |  Ranks higher than 82% of same-terrain segments
Model: Random Forest, part14_segment_year_v1
This is a research ranking signal, not a calibrated probability and not
a cause-of-landslide explanation. See "what this model weighs" below.

What this model weighs most heavily (across all training data):
  Elevation  ############  27%
  Slope      ##########    23%
  Terrain (plain)  ######  15%
  ...
```

Explicitly distinguishing "model feature contribution" (the bars above)
from "cause of landslide" (never claimed).

---

## 11. Model versioning

The application must be able to answer, for any ML signal it surfaces:
*which* model produced it, trained on *what*, with *what* feature schema.
All of this already exists in the artifacts and just needs to be read
and checked, not invented:

- **Model name/type:** `model_manifest.json.models.random_forest.sklearn_class`
- **Experiment/model version:** `model_manifest.json.experiment_id`
  (`"part14_segment_year_v1"`) — the natural version string to log/expose.
- **Training dataset version:** `model_manifest.json.training_dataset.dataset_sha256`
  — a hash of the exact CSV the model was fit on.
- **Feature schema version:** `feature_schema.json.feature_names_in_order`
  itself acts as the schema contract; the adapter should hash or otherwise
  fingerprint this list at load time and refuse to serve (fall back per
  Section 8) if it doesn't match what the adapter code expects — this is
  the concrete mechanism behind `feature_schema.json`'s own warning about
  column mismatches.
- **Artifact hash:** not currently generated for the `.joblib` files
  themselves (only the dataset CSV is hashed); worth adding a
  `sha256(random_forest_model.joblib)` check at load time in the actual
  implementation part, so a corrupted or swapped file is caught at
  startup rather than producing silently-wrong scores.
- **Git commit:** `model_manifest.json.git_commit` — already recorded.

**Design rule:** the production application must load the manifest, verify
the feature schema fingerprint, and refuse (fall back to Section 8's
"unavailable" path) rather than guess, on any mismatch — "never silently
use an incompatible model artifact," per the task's own Section 9 heading.

---

## 12. Configuration (design only — not implemented)

```python
# app/config.py — conceptual additions, NOT implemented in this part

ML_RISK_ENABLED = False          # master switch; False = today's exact behavior
ML_TIE_BREAK_ENABLED = False     # separate, narrower switch — see Section 9;
                                  # meaningless/ignored while ML_RISK_ENABLED is False
ML_ARTIFACT_DIR = "app/data/ml/artifacts"
ML_EXPECTED_EXPERIMENT_ID = "part14_segment_year_v1"   # version pin — see Section 11
ML_RAINFALL_APPROXIMATION_MODE = "unavailable"  # "unavailable" (Option B, default)
                                                  # | "prior_year_climatology" (Option A,
                                                  #   explicit opt-in only — see Section 7)
```

When `ML_RISK_ENABLED = False`: no ML module is even imported by the
request path (import deferred/lazy inside the adapter, not at
`app/core` import time), so there is zero behavioral or performance
difference from today — genuinely "behaviorally equivalent," not just
"produces the same numbers."

When `True`: the adapter attempts inference per Sections 7-8 and attaches
results additively per Section 4.

---

## 13. Security / reliability considerations

- **No new attack surface on user input path:** the model consumes only
  server-side `RoadSegment` fields and a server-side date — never raw
  request body fields directly (i.e., a client cannot pass arbitrary
  feature values into `predict_proba()`).
- **Artifact integrity:** loading a `.joblib` file executes arbitrary
  pickle-based deserialization by design (this is a known general
  `joblib`/`pickle` property, not specific to this project) — the
  artifacts directory must remain a trusted, version-controlled,
  code-review-gated location, never a path writable by an untrusted
  process or populated from an unauthenticated upload endpoint.
- **Resource bounds:** load the model once per process (mirrors
  `get_default_rainfall_loader()`'s singleton pattern already used
  elsewhere in this codebase), not per-request — 300 shallow
  (`max_depth=5`) trees is cheap to evaluate per call, but repeated
  `joblib.load()` from disk is not free.
- **No PII/sensitive data involved** — all inputs are public geographic/
  infrastructure data already loaded into `RoadSegment`.
- **Fallback-on-failure (Section 8) is itself a reliability property**,
  not just a UX nicety: it guarantees the ML path cannot become a new
  single point of failure for route calculation, which remains this
  system's core safety-relevant function.

---

## 14. Known blockers

1. **BLOCKER — Rainfall feature availability (Section 5/7).** 4 of 21
   trained features require a full-year rainfall archive aggregate that
   is (a) not wired into any production code path today and (b) not
   guaranteed present in every deployment (kept out of version control).
   Computing a true "current year" value is additionally impossible for
   an in-progress year by construction. **Resolution requires an explicit
   decision** (Option A approximation vs. Option B "unavailable," Section
   7) — this design defaults to Option B and does not invent a rainfall
   number to unblock inference.
2. **Caveat, not a blocker — lifetime vs. prior-year historical counts
   (Section 5).** Using `RoadSegment.historical_landslide_count`/
   `nearest_landslide_distance_m` as the "prior" features is defensible
   for a "predict for today" use case but relies on an assumption (GSI
   inventory not silently updated without a corresponding refresh) that
   should be asserted in code (e.g. a startup check that `as_of_date` is
   after the training data's max event year) rather than left implicit.
3. **No artifact hash check exists yet** for the `.joblib` files
   themselves (Section 11) — should be added when this is actually
   implemented, not before.
4. **No per-instance explanation method exists** (Section 10) — global
   feature importance is available and honest to show; per-prediction
   causal-style explanation is not, and adding one (SHAP etc.) is
   explicitly out of scope for this part.
5. **`ml_dataset_inspection_part14.md`'s unresolved reporting/observation
   bias** (mountain sections may be over-represented in GSI surveying
   effort, not just genuinely riskier) is a modeling limitation inherited
   as-is — no production integration design can fix a training-data
   property; it simply means the "advisory, not authoritative" framing in
   Section 3 is load-bearing, not decorative.

---

## 15. Recommended implementation plan (staged, NOT implemented now)

- **Part 15A — ML inference service.** New module (e.g.
  `app/core/ml_risk_signal.py` or `app/data/ml/inference_service.py`):
  loads artifacts once, verifies manifest/schema fingerprint (Section 11),
  builds a feature row from a `RoadSegment` + `as_of_date` using the exact
  `feature_matrix.py` encoding, calls `predict_proba()`, computes the
  terrain-relative percentile (Section 6) against a cached reference
  population, and implements every fallback in Section 8. Pure function
  w.r.t. its inputs; no import from `app/core/risk_engine.py`,
  `routing_engine.py`, `reroute_service.py`, or `hazard_state.py`, and
  none of those import it either — kept a leaf module, consistent with
  how `risk_engine.py` today has zero routing knowledge.
- **Part 15B — Config + feature-flag plumbing.** Add
  `ML_RISK_ENABLED`/`ML_TIE_BREAK_ENABLED`/related constants to
  `app/config.py` (additive only — no existing constant touched).
- **Part 15C — API exposure.** A new, additive field on the relevant
  response models (likely `RiskResult`/`RouteRiskProfile` gain an
  `Optional[MLRiskSignal] = None` sibling field, or a wrapping response
  model is introduced instead if changing those models is judged too
  invasive) plus the call-site wiring in `routes_routing.py`/
  `routes_network.py` that is `if ML_RISK_ENABLED: ...` gated. This is the
  first part that would touch a currently-off-limits file, and only
  additively (new optional field, new conditional branch) — still subject
  to explicit review/approval before starting.
- **Part 15D — Routing interaction (display-only).** Thread the ML signal
  into `RiskAwareRouteResult`/`RouteDecision`'s existing `reasons`
  mechanism as informational text only, per Section 9's Stage 1 — no cost
  or exclusion changes.
- **Part 15E — UI explanation.** New, visually distinct frontend block
  (Section 10's mockup), added beside — not inside — the existing
  `RiskBreakdown.jsx` output.
- **Part 15F (separately approved, later) — Tie-break routing.** Section
  3/9's narrow `ML_TIE_BREAK_ENABLED` option, only after 15A-E have shipped
  and been observed, and only with its own explicit sign-off.

Each stage should ship with `ML_RISK_ENABLED=false` as the merged default,
verified by the "ML disabled" test category below, before being flipped on
in any environment.

---

## 16. Testing plan (design only)

| Test | What it verifies |
|---|---|
| ML enabled, all features available | Adapter returns a well-formed `MLRiskSignal` with `value` in [0,1] and a percentile in [0,100]; existing `RiskResult` for the same segment is byte-for-byte identical to the ML-disabled case |
| ML disabled | No ML module imported/executed on the request path at all (can be asserted via import-time mocking/spy); response shape excludes the optional field entirely (not just `null`, if that distinction matters to the chosen schema) |
| Model artifact missing/corrupt at startup | Adapter initialization fails gracefully; `ML_RISK_ENABLED` effectively becomes inert; no exception escapes to the API layer; existing routes still work |
| Missing rainfall (the default/current state) | With `ML_RAINFALL_APPROXIMATION_MODE="unavailable"`, `MLRiskSignal` is `None` for every request, and this is distinguishable in logs/metrics from a "model broken" failure |
| Invalid/malformed feature vector | Deliberately corrupt one feature name/order in a test double; adapter detects the schema mismatch and returns `None`, never calls `predict_proba()` on mismatched columns |
| Deterministic inference | Same `RoadSegment` + same `as_of_date` -> identical `MLRiskSignal.value` across repeated calls (both models are already deterministic — `random_state=42`, no randomness at inference time) |
| Schema mismatch (manifest fingerprint) | Artifact's `feature_names_in_order` deliberately altered in a test fixture -> adapter refuses to serve, falls back, does not silently reindex incorrectly |
| Existing behavior unchanged when ML disabled | Full existing test suite (currently 627 tests) continues to pass unmodified; a new targeted test asserts `risk_engine.py`/`routing_engine.py`/`reroute_service.py` outputs for a fixed fixture are identical whether or not the (disabled) ML config flag is present |
| Route calculation with ML available | `/routes/calculate-risk-aware` (or wherever 15C lands) returns the same `recommended_route`/`outcome` as today, plus a well-formed additional ML field |
| Route calculation with ML unavailable (any Section 8 failure mode) | Same endpoint returns identical routing outcome to the ML-disabled case, with the additional field explicitly absent/`None`, and no elevated latency/error rate |
| NaN/invalid score guard | A test double model that returns `nan` or a value outside [0,1] -> adapter treats it as `None`, never propagates a NaN into any response body |

---

## 17. Hard boundaries respected in this part

No files were modified. Confirmed by running the full backend test suite
at the end of this pass:

```
627 passed, 12 warnings in 157.67s
```

matching the expected baseline exactly. No model was retrained, no new
data was collected, no hyperparameter search was run, no SHAP/large new
dependency was added, and no integration code was written — this document
is the entire deliverable for Part 15.
