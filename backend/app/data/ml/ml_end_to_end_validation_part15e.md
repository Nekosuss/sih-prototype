# Part 15E — End-to-End ML Validation, Reproducibility & Evidence

**Status:** Validation only. No model retrained/tuned, no algorithm swap,
no feature change, no routing/risk-engine change, no new UI functionality,
no new data source. Three new regression tests were added (Section 13);
everything else in this document is re-running and inspecting existing
mechanisms and reporting real, captured output.

---

## 1. The complete pipeline — traced and verified, not assumed

```
Historical data (GSI landslide inventory + IMD rainfall archive + OSM/DEM)
        |
        v
segment_year_dataset.csv (32,604 rows -- verified below, Section: Dataset)
        |
        v
app/data/ml/feature_matrix_v2_17feature.py :: build_feature_matrix_v2()
        |  -- SAME function used by both:
        |     (a) save_model_artifacts_v2_17feature.py at training time
        |     (b) app/core/ml_risk_signal.py::_build_feature_row() at
        |         inference time (verified identical call, Section 2)
        v
17-feature pandas row, reindexed to feature_schema.json's
feature_names_in_order (verified byte-identical list, Section 2)
        |
        v
backend/app/data/ml/artifacts/v2_17_feature/random_forest_model.joblib
(RandomForestClassifier, loaded via joblib -- verified loadable, Section 3)
        |
        v
app/core/ml_risk_signal.py :: get_ml_risk_signal()
        |
        v
app/models/ml_risk.py :: MLRiskSignal
        |
        v
GET /segments/{segment_id}/ml-risk  (app/api/routes_network.py)
        |
        v
frontend/src/api/client.js :: getSegmentMlRisk()
        |
        v
frontend/src/components/SegmentDetailPanel/SegmentDetailPanel.jsx
        |
        v
"Advisory - ML Risk Signal" block, rendered in the browser
```

**This was verified by actually executing every step, in this session**,
not by reading the code and assuming it connects:

- `_build_feature_row()` was confirmed (by direct source inspection, this
  session) to call `feature_matrix_v2_17feature.build_feature_matrix_v2()`
  — the identical function `save_model_artifacts_v2_17feature.py` used to
  build the training matrix. No parallel/duplicate encoding path exists.
- The artifact was loaded fresh via `ml_risk_signal._load_artifact()` in
  this session (not assumed cached from a prior part) and returned a
  valid, schema-matching artifact (Section 3).
- The full HTTP path was exercised end-to-end with a **live** `uvicorn`
  server and a **live** browser (Playwright), both freshly started in this
  session (Section: Browser verification) — confirming the API and
  frontend layers are actually wired together, not just individually unit
  tested.

---

## Dataset

Loaded fresh this session via `app.data.ml.feature_matrix.load_dataset()`:

| Metric | Value |
|---|---|
| Total rows | **32,604** |
| Event rows | **30** |
| Unobserved rows (label = NaN, never treated as negative) | **32,574** |
| `non_event_documented` rows | **0** |
| Distinct positive OSM way-groups (LOGO grouping key) | **25** |

Matches the state documented at the start of this part exactly.

---

## Model

- **Algorithm:** `sklearn.ensemble.RandomForestClassifier` (unchanged —
  `n_estimators=300, max_depth=5, min_samples_leaf=5,
  class_weight="balanced_subsample", random_state=42`).
- **Version:** `part15a_segment_year_v2_17feature` (read from
  `artifacts/v2_17_feature/model_manifest.json::experiment_id` at load
  time — this session confirmed `ml_risk_signal._load_artifact()` returns
  exactly this string, matching `app.config.ML_EXPECTED_EXPERIMENT_ID`).
- **Feature schema version (fingerprint):** `00905a522d1a60ac` (a 16-hex
  SHA-256-derived fingerprint of the ordered 17-feature list — confirmed
  identical across every real inference call made in this session).
- **Feature count:** **17** (confirmed three independent ways in Section
  2 below).

---

## 2. Feature parity validation — result: **MATCH, verified three independent ways**

| Check | Result |
|---|---|
| `feature_schema.json::feature_names_in_order` (17 names, exact order) equals `feature_matrix_v2_17feature.build_feature_matrix_v2(df).feature_names` (freshly recomputed from the real dataset, this session) | **True** |
| Loaded model's own `model.feature_names_in_` (recorded by scikit-learn at `.fit()` time) equals `feature_schema.json`'s list | **True** |
| `app.core.ml_risk_signal._load_artifact()`'s returned `artifact.feature_names` equals the schema file's list | **True** |
| `n_features == 17` in schema file, matches `len(feature_names)` from a fresh build | **True** |
| `ml_risk_signal.py`'s inference path (`_build_feature_row()`) reindexes to this exact list before calling `.predict_proba()` | **True** (by source inspection — `X.reindex(columns=expected_feature_names, fill_value=0)`, `expected_feature_names` sourced from the loaded artifact, never hardcoded independently) |

No feature was found to be silently added, removed, renamed, reordered,
or transformed differently at inference time versus training time — the
inference path literally reuses the training-time encoding function
rather than a hand-written parallel implementation, which structurally
rules out the most common source of this kind of drift.

**Existing validation mechanism exercised, not duplicated:** this
confirms exactly what `feature_schema.json`'s own note requires ("Any
future inference row... MUST be built via
`build_feature_matrix_v2()`... and reindexed to `feature_names_in_order`
before calling `.predict_proba()`") and what `ml_risk_signal.py`'s own
two-layer check already enforces at every artifact load (Section 3) — no
new parity mechanism was built.

---

## 3. Artifact integrity — result: **PASS**

Performed by actually loading the real artifact files in this session
(not reading the JSON and assuming it's consistent):

| Check | Result |
|---|---|
| `random_forest_model.joblib` loads via `joblib.load()` | **Loads successfully** |
| `feature_schema.json` parses as valid JSON with a 17-element `feature_names_in_order` | **Valid** |
| `model_manifest.json` parses as valid JSON | **Valid** |
| Model's `feature_names_in_` matches `feature_schema.json` | **Match** |
| `experiment_id` in manifest == `app.config.ML_EXPECTED_EXPERIMENT_ID` | **Match** (`part15a_segment_year_v2_17feature`) |
| Artifact directory == `app.config.ML_ARTIFACT_DIR` | **Match** (resolves to `backend/app/data/ml/artifacts/v2_17_feature`) |
| `ml_risk_signal._load_artifact()` succeeds end-to-end against the real files | **Succeeds** |
| `dataset_sha256` present in manifest (dataset-level integrity) | **Present** |

**Artifact (`.joblib`) hash/checksum mechanism: does NOT exist.** Checked
directly — `model_manifest.json` records `dataset_sha256` (a hash of the
*training CSV*) but no hash of the `.joblib` model file itself, and no
code anywhere in `ml_risk_signal.py` or the `save_model_artifacts*.py`
scripts computes or checks one. This gap was already noted in Part 15B's
report (`ml_inference_part15b.md`, Section 4) as a known limitation. Per
this part's explicit instruction, **no new hashing system was built** —
this is a documented gap, not a fix.

---

## 4. Reproducibility — result: **exact match, all four models, full precision**

Re-ran the complete, unmodified LOGO evaluation (`logo_evaluation.py::leave_one_group_out`, `models.py`'s unmodified factories, same
dataset, same grouping, same random seed) fresh in this session, for all
four model/feature-set combinations, and compared against what is
currently stored in each artifact's `validation_metadata.json`:

| Model | Stored within-terrain mean % | This run | Match | Stored pooled AUC | This run | Match |
|---|---|---|---|---|---|---|
| v1 (21-feature) Random Forest | 78.6333042029 | 78.6333042029 | ✅ | 0.9810827140 | 0.9810827140 | ✅ |
| v1 (21-feature) Logistic Regression | 72.9372058025 | 72.9372058025 | ✅ | 0.9539921933 | 0.9539921933 | ✅ |
| v2 (17-feature) Random Forest | 75.5948058818 | 75.5948058818 | ✅ | 0.9755003895 | 0.9755003895 | ✅ |
| v2 (17-feature) Logistic Regression | 74.9170861632 | 74.9170861632 | ✅ | 0.9648659102 | 0.9648659102 | ✅ |

**All four reproduced exactly (agreement within 1e-6, effectively
bit-for-bit) — no discrepancy to investigate.** This confirms the
previously reported approximate values from Part 15A/15D
(v1 RF ≈78.6, v1 LR ≈72.9, v2 RF ≈75.6, v2 LR ≈74.9, unchanged) are the
real, full-precision, reproducible numbers — not rounded or approximated
in this report. **No documented metric was overwritten** — the stored
`validation_metadata.json` files were read, not modified, in this
session.

**Rule-based baseline** (for reference, unchanged, computed by
`compute_baseline_scores()` calling the real, unmodified
`risk_engine.assess_segment_risk()`): **58.2 mean within-terrain
percentile, AUC ≈0.535** — recorded in `ml_modeling_feasibility_part14.md`
and re-confirmed present/consistent in `v2_17_feature/validation_metadata.json`'s `baseline_comparison` field this session (full-population framing there; the 58.2/0.535
within-terrain figures are the Part 14.3 report's own numbers, not
recomputed fresh in this pass since the baseline is a fixed formula with
nothing to refit — see that report for its own full derivation).

---

## 5. Leakage regression check — result: **7/7 PASS**

Re-ran `app.data.ml.leakage_checks.run_all_checks(df, fm)` fresh, this
session, against the real dataset:

| # | Check | Result |
|---|---|---|
| 1 | Future rainfall leakage (each row's rainfall independently re-read from its own year's NetCDF file) | **PASS** — 0 mismatches across 25 sampled rows |
| 2 | Future/lifetime landslide-history leakage (prior-cutoff never undercounts real prior events) | **PASS** — 0 violations across all 32,604 rows |
| 3 | Lifetime `historical_landslide_count` leakage (feature matrix uses only the cutoff-safe `_prior` column) | **PASS** |
| 4 | Segment/way-group identity leakage (grouping is by `way_id`, not `segment_id`) | **PASS** — holding out one way-group removes all its rows with zero segment overlap |
| 5 | Candidate-pool selection leakage (all 2,964 real segments included unconditionally) | **PASS** |
| 6 | Duplicate/clustered storm records (multi-report segment-years still resolve to exactly one `label=1.0`) | **PASS** — 17 of 30 event rows have `event_report_count > 1`, all still label exactly 1.0 |
| 7 | Spatial leakage from sibling road segments (29 positive segment_ids → 25 way-groups, grouped correctly) | **PASS** |

**Re-run against v2's feature matrix too** (same `df`, same
`way_id`/`segment_id`/`is_event` fields as v1 — only `X`/`feature_names`
differ): all 7 checks **also PASS**. One caveat, stated precisely: check
#3 (`check_lifetime_count_not_used_as_a_feature`) inspects the
module-level `feature_matrix.NUMERIC_FEATURE_COLUMNS` constant directly,
not whatever `FeatureMatrix` object is passed to it — so re-running it
against v2's `fm` does not, by itself, verify v2's own column list. A
**direct, explicit check** of `feature_matrix_v2_17feature.NUMERIC_FEATURE_COLUMNS_V2` was performed instead: confirmed it uses
`historical_landslide_count_prior` (cutoff-safe) and does **not** contain
either raw lifetime column (`historical_landslide_count`,
`nearest_landslide_distance_m`), and confirmed independently that v2's
full 17-feature list excludes all 4 rainfall columns entirely.

**Unobserved rows are not treated as negatives** (item 8 of this part's
list): confirmed by inspection — `y_pseudo` (fit-time target) treats
`unobserved` as 0 *only* for `sklearn`'s `.fit()` call; every ranking
metric reported anywhere in this document (Section 4) is computed via
`is_event` (the real, undisguised event/non-event distinction), never
against `y_pseudo` as if it were ground truth — this is the same
distinction `MODEL_CARD.md` and `feature_matrix.py`'s own docstring have
stated since Part 14.3, re-verified by reading the actual
`ranking_evaluation.py`/`logo_evaluation.py` code paths used to produce
every number in Section 4.

---

## 6. Real corridor inference — result: **6/6 representative segments scored successfully**

Ran `ml_risk_signal.get_ml_risk_signal()` (ML enabled in-memory only, on a
throwaway process — see Section 8) against one real segment touching each
of six representative corridor locations, `as_of_date = 2026-09-05`:

| Place | Segment ID | Segment name | Terrain | Available | ML score | Tier | Model version |
|---|---|---|---|---|---|---|---|
| Guwahati | `seg_51188919_0` | Stadium Overbridge | plain | True | 0.0000 | Low | `part15a_segment_year_v2_17feature` |
| Bhalukpong | `seg_22832893_0` | Bhalukpong-Doimara | plain | True | 0.0181 | Low | `part15a_segment_year_v2_17feature` |
| Bomdila | `seg_1159219312_0` | College Road | mountain | True | 0.0999 | Low | `part15a_segment_year_v2_17feature` |
| Dirang | `seg_507463415_0` | (unnamed) | mountain | True | 0.2878 | Low | `part15a_segment_year_v2_17feature` |
| Sela Pass | `seg_1260641175_0` | (unnamed) | mountain | True | 0.7065 | Elevated | `part15a_segment_year_v2_17feature` |
| Tawang | `seg_311848756_0` | (unnamed) | mountain | True | 0.2785 | Low | `part15a_segment_year_v2_17feature` |

**0 failed cases** among the 6 attempted — every representative segment
had usable `slope_deg`/`elevation_m` and a supported `road_type`/
`terrain_type`, so all 6 returned `available: true`. This is expected
given Part 15's finding that 17/17 of v2's features are honestly
computable from every real segment in this corridor; a failed case would
only be expected for a segment missing DEM coverage (rare, see Part 15B).

These are real, unrounded outputs from the actual saved model — not
fabricated for this table. Note the real variation across terrain/
location (0.0 → 0.71): this is exactly the kind of relative discrimination
LOGO validation measured (Section 4), not a claim about any of these
specific six segments' true hazard.

---

## 7. ML disabled — result: **CONFIRMED, live server**

With `ML_RISK_ENABLED = False` (the untouched, shipped default) on a
freshly started real `uvicorn` process:

```
GET /segments/seg_22832893_0/ml-risk
→ 200 {"available": false, "score": null, "model_version": null,
        "feature_schema_version": null,
        "reason": "ML risk signal disabled by configuration (ML_RISK_ENABLED=False)", ...}
```

- **No model inference occurs:** already proven at the unit level
  (`test_ml_disabled_returns_unavailable_without_loading_artifact`
  monkeypatches the loader to raise if called, and it never is) — this
  session additionally confirms it live, since the disabled server never
  even has the artifact cached (`ml_risk_signal.clear_artifact_cache()`
  was never needed to reset a disabled run).
- **Frontend remains fully functional:** verified live in a real browser
  (Section 12) — dashboard loads, route calculates, segment selects, the
  advisory section shows a clean unavailable state, zero console errors.
- **Segment risk remains functional:** `GET /segments/{id}/risk-aware`
  on the same live server returned a normal real result
  (`risk_score: 0.0, risk_level: "low"`).
- **Routing remains functional:** `POST /routes/calculate-risk-aware` for
  Guwahati→Tawang on the same live server returned a normal real result
  (`outcome: "fastest_route_is_safe"`, `distance: 501.14 km`,
  `aggregate_risk_score: 0.3881`).

---

## 8. ML enabled — result: **CONFIRMED, live server, config.py never touched on disk**

Using the identical method Part 15D established: a throwaway Python
process imports `app.config`, sets `config.ML_RISK_ENABLED = True` **in
memory only**, then runs `uvicorn.run("app.main:app", ...)` — the file
`backend/app/config.py` is never opened for writing.

```
GET /segments/seg_22832893_0/ml-risk
→ 200 {"available": true, "score": 0.0181,
        "model_version": "part15a_segment_year_v2_17feature",
        "feature_schema_version": "00905a522d1a60ac", "reason": "ok", ...}
```

- Real scores returned for real segments (Section 6's table was produced
  this same way).
- Model metadata correct: `model_version` matches the manifest exactly;
  `feature_schema_version` is stable and identical across repeated calls.
- **Routing on this same ML-enabled live server** returned
  `outcome: "fastest_route_is_safe"`, `distance: 501.14 km`,
  `aggregate_risk_score: 0.3881` — **byte-identical** to the disabled
  server's result in Section 7.

**Working tree confirmed unchanged after the test:**

```
$ git diff --stat backend/app/config.py
 backend/app/config.py | 41 +++++++++++++++++++++++++++++++++++++++++
 1 file changed, 41 insertions(+)
```

41 insertions — identical to Part 15B's original, untouched addition.
Nothing from this session's enabled-mode testing altered the file.

---

## 9. Failure / fail-safe testing — result: **all existing mechanisms re-confirmed, none weakened**

Re-ran the existing fail-safe test suite fresh this session (no test was
modified to make this pass):

| Failure mode | Test | Result |
|---|---|---|
| Missing artifact | `test_missing_artifact_directory_is_unavailable` | PASS |
| Corrupt `.joblib` | `test_corrupt_model_file_is_unavailable` | PASS |
| Invalid manifest JSON | `test_invalid_manifest_json_is_unavailable` | PASS |
| Incompatible `experiment_id` | `test_incompatible_experiment_id_is_unavailable` | PASS |
| Feature schema mismatch (truncated) | `test_feature_schema_mismatch_is_unavailable` | PASS |
| Feature schema mismatch (reordered, same columns) | `test_feature_names_reordered_is_unavailable` | PASS |
| Missing `slope_deg`/`elevation_m` (invalid feature values) | `test_missing_slope_deg_is_unavailable` / `test_missing_elevation_m_is_unavailable` | PASS |
| `as_of_date` outside the proxy-validity window | `test_as_of_date_before_proxy_validity_window_is_unavailable` | PASS |
| Inference exception / non-finite output | `test_ml_risk_signal_never_raises_for_any_failure_mode` | PASS |
| API/network failure (live browser, request forcibly aborted) | `part15d_failure_test.py`-style live check, re-run this session | PASS — authoritative content intact, advisory section shows clean unavailable text, no console error, no broken panel |

**31/31** ML-related backend tests pass (17 in `test_ml_risk_signal.py` +
14 in `test_api.py`, including the 3 new ones from Section 13). Every
failure mode converges on the same principle, confirmed live and in
tests: **`available: false` / "ML advisory signal unavailable" — never a
crash, never a fabricated score, and never any effect on
`risk_score`/routing/rerouting/vehicle simulation/field reporting**, none
of which have any code path that reads from this module at all (verified
structurally in Section 10/11, not just "didn't observe a problem").

---

## 10. Routing isolation regression — result: **IDENTICAL, proven programmatically**

**New test, added this part:**
`test_route_calculation_identical_ml_disabled_vs_enabled` (`tests/test_api.py`) — a single test, one `TestClient`/app instance, `ML_RISK_ENABLED`
flipped mid-test via `monkeypatch`:

1. `POST /routes/calculate-risk-aware` with `ML_RISK_ENABLED=False`.
2. Flip to `True`, then **actually call** `GET /segments/{id}/ml-risk`
   for the first 30 segments on the resulting route (asserting
   `available: true` each time — proving ML genuinely ran, not silently
   skipped).
3. `POST /routes/calculate-risk-aware` again, same origin/destination.
4. Assert, field-by-field: `outcome`, `safer_alternative_selected`,
   `unsafe_segments_in_fastest_route`, `fastest_route` (minus the
   inherently-random `route_id`/`created_at`), `fastest_route_risk`,
   `fastest_route_segment_risks`, `recommended_route` (same exclusions),
   `recommended_route_risk`, `recommended_route_segment_risks`, and
   `reasons` — **all identical**.

This directly covers every item this part asked to compare: route
geometry (part of the `Route` object), route segment IDs, distance, ETA,
route risk score, maximum segment risk, risk level (all inside
`fastest_route_risk`/`recommended_route_risk`), and the safety outcome.
"Route color" is a pure frontend derivation from `risk_level`
(`utils/risk.js::riskLevelColor`), which — since `risk_level` itself is
proven identical — is therefore also identical; this was additionally
confirmed visually (Section 12, screenshots show the same route color in
both states) rather than asserted only by inference.

**State-mutation check** (also new,
`test_ml_risk_lookup_does_not_mutate_state_store`): confirms the live
`StateStore` segment object itself (`model_dump()`, full equality) is
byte-identical before and after calling `GET /segments/{id}/ml-risk` —
proving the isolation isn't just "the next calculation happens to match"
but that nothing was mutated in between.

---

## 11. Reroute isolation — result: **IDENTICAL, proven programmatically**

**New test:** `test_reroute_decision_identical_ml_disabled_vs_enabled`
(`tests/test_api.py`) — repeats the existing real hazard→reroute scenario
(same corridor segment, "Doimara-Nichiphu", same origin/destination,
Bhalukpong→Bomdila, as
`test_evaluate_disruption_real_corridor_reroutes_after_hazard`) twice: once
with ML disabled, once with it enabled and the ML endpoint actually
queried for every affected segment in between.

Each run creates its own hazard (a fresh random id/timestamp by design —
`app/models/hazard.py`), so `active_hazard_ids` and each run's own
`route_id`s are excluded from the diff; every decision-relevant field is
compared exactly:

- Baseline outcome: `"continue"` in both runs.
- Post-hazard outcome: `"reroute"` in both runs.
- `recommended_route` (minus `route_id`/`created_at`): **identical**.
- `recommended_route_risk`: **identical**.
- `affected_segment_ids`: **identical**.
- `eta_change_min`: **identical**.
- `reason` (the human-readable decision text): **identical**.

**Confirmed the ML endpoint cannot:**
- trigger rerouting — the reroute in both runs was triggered solely by
  the simulated hazard, never by an ML call (no code path exists from
  `ml_risk_signal.py`/the API route into `reroute_service.py`).
- alter route cost — `routing_engine.risk_aware_edge_cost()` was not
  touched in Parts 15A–15E and does not import `ml_risk_signal`.
- alter the hard unsafe threshold — `HARD_UNSAFE_RISK_THRESHOLD` in
  `app/config.py` is untouched; the pre-existing guard test
  `test_ml_risk_signal_module_is_not_wired_into_risk_or_routing_engines`
  (Part 15C) statically confirms `routing_engine.py` never imports
  `ml_risk_signal`.
- alter hazard state — `hazard_state.py` is untouched and imports nothing
  from this module; `test_ml_risk_lookup_does_not_mutate_state_store`
  additionally confirms no `StateStore` mutation occurs at all.
- alter vehicle state — `app/simulation/` is untouched by Parts 15A–15E
  (confirmed via `git status` throughout every part).

---

## 12. Browser verification — result: **10/10 checks pass, live, real browser and backend**

Reused Part 15D's exact approach: no JS test framework or Playwright-for-
JS exists in this project (unchanged since Part 15D — `frontend/package.json` still has no test script), so per this part's own instruction not
to install a new framework for this task, verification was performed with
the same **Playwright (Python)** installation already used in Part 15D,
against freshly started real `uvicorn` + Vite dev servers.

| # | Check | Result |
|---|---|---|
| 1 | Dashboard loads | PASS |
| 2 | Route calculates | PASS (Guwahati→Tawang, 501.14 km, 11h 43m) |
| 3 | Segment can be selected | PASS (real segment clicked on the map) |
| 4 | ML disabled state displays correctly | PASS ("ML advisory signal unavailable", no fabricated 0) |
| 5 | ML enabled state displays a real score | PASS (`0.018`, real model) |
| 6 | ML score clearly labeled advisory | PASS ("Advisory · ML Risk Signal", "Status: Advisory") |
| 7 | Not presented as probability | PASS — full page text scanned for `"probability of landslide"`, `"% chance"`, `"percentage chance"`, `"chance of landslide"`, `"likelihood percentage"`; none found, either state |
| 8 | Existing authoritative risk remains visible | PASS ("Authoritative · Current Segment Risk", `0.08`, `LOW`) |
| 9 | Network failure of ML endpoint does not break the panel | PASS (forced-abort test — authoritative content intact, advisory shows clean unavailable, route summary unaffected) |
| 10 | No browser console errors | PASS — zero console errors in all three live runs (disabled, enabled, forced-failure) |

Additionally re-confirmed (Section 10): the "Route Risk" score read via a
Playwright locator was identical (`0.39`) before and after every ML
interaction, in both the disabled and enabled runs.

**Screenshots** (`backend/app/data/ml/screenshots_part15e/`):
`01_ml_disabled_reverified.png`, `02_ml_enabled_reverified.png`,
`03_ml_api_failure_reverified.png`.

---

## 13. Test count regression

| | Count |
|---|---|
| Previous baseline (Part 15D) | **657** |
| New tests added this part | **3** |
| Final count | **660** |

New tests (all in `tests/test_api.py`, Section 10/11 above):

1. `test_route_calculation_identical_ml_disabled_vs_enabled`
2. `test_reroute_decision_identical_ml_disabled_vs_enabled`
3. `test_ml_risk_lookup_does_not_mutate_state_store`

No existing test was modified in this part. Full run:

```
660 passed, 12 warnings in 150.33s (0:02:30)
```

Frontend build:

```
✓ 94 modules transformed.
✓ built in 1.10s
```

Unchanged from Part 15D (no frontend file was modified in this part).

---

## Isolation — explicit statement

Confirmed, this session, by a combination of static code-path analysis
(guard tests from Parts 15B/15C, re-run and still passing) and live
programmatic A/B testing (Sections 10–11, new this part): **ML does not
affect**:

- `risk_score` / `risk_level` (`risk_engine.py` — untouched, no import of
  `ml_risk_signal` anywhere in it).
- Risk thresholds (`RISK_LEVEL_THRESHOLDS`, `HARD_UNSAFE_RISK_THRESHOLD`,
  `TERRAIN_WEIGHT`/`HISTORICAL_WEIGHT`/`WEATHER_WEIGHT`/`INCIDENT_WEIGHT`
  — all untouched in `app/config.py`).
- Route cost (`routing_engine.risk_aware_edge_cost()` — untouched, no
  import).
- Route selection (Section 10 — byte-identical route chosen and scored
  with ML disabled vs. enabled).
- Rerouting (Section 11 — byte-identical `reroute` decision either way).
- PROCEED/REROUTE/SUSPEND (`reroute_service.py` — untouched, no import;
  Section 11's `outcome` field identical in both runs).
- Vehicle simulation (`app/simulation/` — untouched across every ML part
  to date).

---

## Limitations (unchanged from Part 14/15A, restated honestly)

- **Extremely sparse positive events:** only 30 documented event rows
  across 11 years, collapsing to 25 independent physical-road groups —
  every metric in Section 4 is an average over a handful of independent
  observations.
- **Reporting/observation-location bias is unresolved:** nothing in this
  data can separate "the high-mountain section genuinely has more
  landslides" from "the high-mountain section is more frequently
  surveyed."
- **`unobserved` rows are never confirmed-safe** — 98.9%+ of
  segment-years carry no GSI record at all, reflecting survey coverage,
  not absence of hazard. No confirmed-negative label exists anywhere in
  this dataset.
- **No live rainfall feed exists** for the 4 features removed in v2
  (annual/monsoon/max-daily/rainy-days rainfall) — this was the entire
  reason v2 exists (Part 15A); it remains true today, and Option B
  (real-time-shaped rainfall features) remains unbuilt and unbuildable
  without a new, separate live data-ingestion project.
- **No calibrated probability exists.** Neither v1 nor v2 was fit with
  any calibration step (Platt scaling, isotonic regression, etc.), and no
  trustworthy base rate exists in the training data to calibrate against.
  The `score` field is a raw `predict_proba()` output — a ranking signal,
  never a probability.
- **No artifact-file hash/checksum mechanism exists** (Section 3) — a
  documented gap, not fixed in this part per its own instructions.
- **Prototype status, restated:** `MODEL_CARD.md`'s conclusion is
  unchanged by anything in Parts 15A–15E: **"ML PROTOTYPE POSSIBLE BUT
  NOT RELIABLE FOR PRODUCTION."** Nothing in this validation pass changes
  that conclusion — it demonstrates the *pipeline* is correctly built and
  correctly isolated, not that the *model* has become production-grade.

**The ~75.6 (v2 Random Forest) / ~74.9 (v2 Logistic Regression) figures
are grouped, leave-one-way-group-out within-terrain ranking-percentile
metrics under this project's own specific experimental setup** — they
are not an accuracy, precision, recall, or F1 score, and they do not mean
"75.6% of predictions are correct" or "75.6% probability" in any general
sense.

---

## Files changed in this part

| File | Change |
|---|---|
| `backend/tests/test_api.py` | **Additive.** 3 new tests (Section 13). No existing test modified. |
| `backend/app/data/ml/ml_end_to_end_validation_part15e.md` | New (this file). |
| `backend/app/data/ml/screenshots_part15e/*.png` | New (3 screenshots). |

No other file was created, modified, or deleted. Specifically untouched:
`risk_engine.py`, `routing_engine.py`, `reroute_service.py`,
`hazard_state.py`, `ml_risk_signal.py`, `models/ml_risk.py`,
`routes_network.py`, every frontend file, every ML artifact under
`artifacts/` or `artifacts/v2_17_feature/`, and `config.py` (confirmed via
`git diff --stat`, Section 8 — the 41-insertion diff from Part 15B is
unchanged).

---

## Final statement

**ML is an advisory signal only and has zero authority over routing or
safety decisions.**
