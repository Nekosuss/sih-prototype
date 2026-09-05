# Part 15B — ML Inference Service

**Status:** Isolated inference infrastructure only. No production
integration. `risk_engine.py`, `routing_engine.py`, `reroute_service.py`,
`hazard_state.py`, every API route, and the frontend are all untouched.
See Section 8 for the confirming test run.

---

## 1. Artifact used (inspected fresh, not from memory)

`backend/app/data/ml/artifacts/v2_17_feature/` — the Part 15A 17-feature
(no-rainfall) Random Forest, chosen per Part 15A's decision because the v1
21-feature model requires 4 rainfall aggregates that cannot be honestly
computed in production. Files read directly:

| File | What it is | Key content |
|---|---|---|
| `random_forest_model.joblib` | The fitted model | `sklearn.ensemble.RandomForestClassifier`, fit on a `pandas.DataFrame` (so it carries its own `feature_names_in_` — used as a second, independent check, see Section 4) |
| `feature_schema.json` | The exact column contract | `feature_names_in_order`: 17 names, exact order (Section 2 below); `n_features: 17`; documents the sentinel/one-hot conventions |
| `model_manifest.json` | Identity/provenance | `experiment_id: "part15a_segment_year_v2_17feature"`, `part: "15A"`, dataset SHA-256, `production_integration_status: "NOT integrated"` |
| `model_config.json` | Hyperparameters | `n_estimators=300, max_depth=5, min_samples_leaf=5, class_weight="balanced_subsample", random_state=42` — identical to v1, no tuning |
| `MODEL_CARD.md` | Intended use / limitations | Inherits v1's full "not intended for autonomous safety decisions / not a calibrated probability" restrictions verbatim |
| (preprocessing) | N/A for this artifact | The Random Forest needs no scaler (tree-based); `logistic_regression_scaler.joblib` exists alongside but is **not loaded** — this service only loads the Random Forest, per this part's explicit instruction |

**Model version identifier used throughout this service:**
`"part15a_segment_year_v2_17feature"` (read from the manifest at load
time, not hardcoded as a display string — see Section 4).

---

## 2. The exact 17 features, in order (from `feature_schema.json`)

```
1.  distance_km
2.  slope_deg
3.  elevation_m
4.  historical_landslide_count_prior
5.  nearest_historical_landslide_distance_m_prior
6.  has_prior_history
7.  road_type_primary
8.  road_type_primary_link
9.  road_type_secondary
10. road_type_secondary_link
11. road_type_tertiary
12. road_type_tertiary_link
13. road_type_trunk
14. road_type_trunk_link
15. terrain_type_hill
16. terrain_type_mountain
17. terrain_type_plain
```

---

## 3. Feature parity — verified, not assumed

Per Part 15/15A's finding, all 17 of these are honestly computable from a
production `RoadSegment` today. The new service
(`app/core/ml_risk_signal.py::_build_feature_row`) builds every value
directly from the real, already-loaded segment:

| Feature | Production source | Notes |
|---|---|---|
| `distance_km` | `RoadSegment.distance_km` | Direct |
| `slope_deg` | `RoadSegment.slope_deg` | **Required non-`None`** — if `None` (no DEM coverage), the service returns `available=False` rather than substituting 0 or any other value |
| `elevation_m` | `RoadSegment.elevation_m` | Same `None`-handling requirement |
| `historical_landslide_count_prior` | `RoadSegment.historical_landslide_count` (lifetime, no year filter) | Documented proxy (Part 15A #4) — see the `as_of_date` gate below |
| `nearest_historical_landslide_distance_m_prior` | `RoadSegment.nearest_landslide_distance_m` (lifetime, nullable) | Same proxy; `None` → `NaN` → the training-time sentinel fill (5000.0) via the reused encoder, never a hand-rolled substitute |
| `has_prior_history` | Derived (`count_prior > 0`) | Computed by the reused encoder, not duplicated by hand |
| `road_type_*` (8) | `RoadSegment.road_type` one-hot | Verified exact category match (Part 15) — the service additionally **rejects** any segment whose `road_type`/`terrain_type` value isn't one of the artifact's own trained categories, rather than silently emitting an all-zero one-hot row |
| `terrain_type_*` (3) | `RoadSegment.terrain_type` one-hot | Same rejection behavior |

**Encoding is reused, not reimplemented.** `_build_feature_row()` calls
`app.data.ml.feature_matrix_v2_17feature.build_feature_matrix_v2()` — the
exact function used to build the training matrix in Part 15A — on a
one-row `DataFrame`, then reindexes the result to the artifact's own
`feature_names_in_order`. This guarantees the *encoding logic itself*
(one-hot prefixes, the 5000.0 sentinel, `has_prior_history`'s derivation)
can never silently drift from what the model was actually trained on,
which a hand-written re-implementation in `app/core` could risk over
time.

**Temporal proxy gate.** Because `historical_landslide_count_prior` is
approximated by a lifetime count (Part 15A's caveat, not a blocker), the
service refuses to produce a signal unless
`as_of_date.year > ML_HISTORICAL_PROXY_VALID_FROM_YEAR - 1` (2025, the
last year the training rainfall archive covers) — this assertion is
checked explicitly at every call, not assumed silently (Part 15's open
item, now closed).

**No value is ever fabricated.** Confirmed by direct inspection of
`_build_feature_row()`: there is no `.fillna(0)`, no `or 0`, no "use a
prior request's cached value," and no substitute for the 4 dropped
rainfall columns anywhere in this code path — they simply do not appear
in the 17-column schema at all.

---

## 4. Model loading behavior

`app/core/ml_risk_signal.py::_load_artifact()` performs, in order:

1. Read `model_manifest.json` — assert `experiment_id ==
   app.config.ML_EXPECTED_EXPERIMENT_ID` exactly.
2. Read `feature_schema.json` — assert `feature_names_in_order` is a
   17-element list.
3. `joblib.load()` the model file — assert it has a `predict_proba`
   method.
4. **Second, independent check:** if the loaded model carries its own
   `feature_names_in_` (sklearn records this when `.fit()` was called
   with a `DataFrame`, which `save_model_artifacts_v2_17feature.py` did),
   assert it matches `feature_schema.json`'s list **exactly** — catches an
   artifact directory left in a half-updated state (e.g. a swapped
   `.joblib` with a stale schema file) that step 2 alone would miss.

Any failure at any step is caught by a single broad
`except Exception` at the call site, logged, and cached as "known
unavailable" (`None`) — so a broken artifact is not silently retried on
every request, and every failure converges on the same outcome (Section
5). The loader result is cached per artifact-directory path (module-level
dict), loaded once per distinct path per process, exactly like
`get_default_rainfall_loader()`'s existing singleton pattern elsewhere in
this codebase — `clear_artifact_cache()` exists only for tests.

---

## 5. Fallback behavior — every failure mode returns an explicit, structured result

`get_ml_risk_signal()` **never raises** for any of the following (verified
by `test_ml_risk_signal_never_raises_for_any_failure_mode` and by direct
manual reproduction of each case before writing tests):

| Failure | Result |
|---|---|
| `ML_RISK_ENABLED = False` (the default) | `available=False, reason="ML risk signal disabled by configuration..."` — the artifact loader is never even called (verified directly, not just implied) |
| Missing artifact directory | `available=False, reason="ML model artifact unavailable or invalid..."` |
| Corrupt `.joblib` file | Same |
| Invalid/unparseable `model_manifest.json` | Same |
| `experiment_id` mismatch (incompatible artifact) | Same, with the specific mismatch logged server-side |
| `feature_schema.json` shorter/reordered vs. the model's own `feature_names_in_` | Same |
| `segment.slope_deg is None` / `elevation_m is None` | `available=False, reason="segment '...': slope_deg unavailable..."` (specific, not generic) |
| `road_type`/`terrain_type` outside trained categories | `available=False`, specific reason (currently unreachable given the exact enum match, but defensively checked) |
| `as_of_date` at/before the proxy-validity cutoff (2025) | `available=False`, reason names the date and the cutoff |
| Any exception during `predict_proba()` | `available=False, reason="model inference raised an exception..."`, logged |
| Non-finite or out-of-[0,1] model output | `available=False, reason="model returned a non-finite or out-of-range score..."` |

**Verified isolation** (test
`test_ml_disabled_does_not_affect_existing_risk_score`): calling
`get_ml_risk_signal()` — enabled or disabled, successful or failing — has
zero effect on what `risk_engine.assess_segment_risk()` returns for the
same segment. Confirmed both by this unit test and by the sample-inference
run in Section 6 below.

---

## 6. Sample inference results (real segments, real model — not mocked)

Run against the real corridor network (`load_network()`), real v2 Random
Forest, `as_of_date = 2026-09-05`:

| segment_id | terrain | historical count | ML available | ML risk signal | rule `risk_score` (before) | rule `risk_score` (after) | unchanged? |
|---|---|---|---|---|---|---|---|
| `seg_44736742_0` | mountain | 0 | True | 0.8273 | 0.0992 | 0.0992 | **True** |
| `seg_44736742_1` | mountain | 0 | True | 0.0692 | 0.0324 | 0.0324 | **True** |
| `seg_44736738_0` | hill | 11 | True | 0.5082 | 0.5167 | 0.5167 | **True** |
| `seg_157598093_0` | hill | 1 | True | 0.6625 | 0.2639 | 0.2639 | **True** |
| `seg_22832893_0` | plain | 0 | True | 0.0181 | 0.0000 | 0.0000 | **True** |
| `seg_38719482_0` | plain | 0 | True | 0.0000 | 0.0000 | 0.0000 | **True** |
| `seg_238491033_0` | mountain | 2 | True | 0.4420 | 0.3124 | 0.3124 | **True** |
| `seg_238491396_0` | mountain | 6 | True | 0.3488 | 0.4113 | 0.4113 | **True** |
| `seg_238496656_2` | mountain | 7 | True | 0.4938 | 0.4263 | 0.4263 | **True** |

Model version for every row: `part15a_segment_year_v2_17feature`.

**These `ML risk signal` values are ranking scores from an uncalibrated
prototype model — not probabilities of a landslide.** Note the two
mountain segments with zero historical count: `seg_44736742_0` gets a
*high* ML signal (0.8273) while its sibling `seg_44736742_1` gets a *low*
one (0.0692) despite sharing terrain and history — consistent with the
model weighing elevation/slope/distance rather than historical count
alone (Part 15A's feature-importance finding). This is exactly the kind
of relative-ranking behavior the model was validated for; it is not a
claim about which segment will actually experience a landslide.

**Every rule-based `risk_score` is bit-for-bit identical before and after
calling the ML service**, confirmed programmatically (`unchanged?` column,
all `True`) — production risk scoring is unaffected by this part's
addition, exactly as required.

---

## 7. What was built

| File | Purpose |
|---|---|
| `backend/app/config.py` | **Additive only.** New section: `ML_RISK_ENABLED = False`, `ML_ARTIFACT_DIR` (points at `v2_17_feature/`), `ML_EXPECTED_EXPERIMENT_ID`, `ML_HISTORICAL_PROXY_VALID_FROM_YEAR`. No existing constant renamed, reweighted, or removed. |
| `backend/app/models/ml_risk.py` | **New.** `MLRiskSignal` Pydantic model — `available`, `score`, `model_version`, `feature_schema_version`, `reason`, `methodology_note`. Does not import or modify `models/risk.py`. |
| `backend/app/core/ml_risk_signal.py` | **New.** The isolated inference service — `get_ml_risk_signal(segment, as_of_date)`, artifact loading/validation, feature-row construction, fail-safe wrapping. Not imported by, and does not import, `risk_engine.py`/`routing_engine.py`/`reroute_service.py`/`hazard_state.py`; not called from any API route. |
| `backend/tests/test_ml_risk_signal.py` | **New.** 17 tests (Section 8). |
| `backend/tests/test_ml_segment_year_dataset.py` | **Updated.** The Part 14 guard test `test_no_ml_module_is_imported_by_production_code` asserted *no* file under `app/core` may import `app.data.ml`, which is now deliberately superseded by this part's explicit instruction to build exactly such a file. Narrowed to allow `ml_risk_signal.py` specifically, and a new companion test (`test_ml_risk_signal_module_is_not_wired_into_risk_or_routing_engines`) was added to keep enforcing the *substantive* invariant the original test existed for: `ml_risk_signal.py` is not imported by, and does not import, any of the four production decision modules, and is not reachable from `app/api` or `app/main.py`. |

No other file was modified. `risk_engine.py`, `routing_engine.py`,
`reroute_service.py`, `hazard_state.py`, every file under `app/api`,
`app/simulation`, and every frontend file are byte-for-byte unchanged
(confirmed via `git status`/`git diff` before writing this report).

---

## 8. Tests

17 new tests in `test_ml_risk_signal.py`, plus 1 new companion test in
`test_ml_segment_year_dataset.py` (the pre-existing guard test there was
narrowed, not removed):

1. `test_valid_v2_artifact_loads` — artifact loads, 17 features in the
   exact expected order, `experiment_id` matches.
2. `test_feature_row_matches_expected_schema_and_values` — a hand-built
   segment produces the exact expected 17-column row with correct values
   and correct one-hot placement.
3. `test_feature_row_no_prior_history_uses_sentinel_and_zero_flag` —
   `has_prior_history=0` and the 5000.0 sentinel appear exactly when
   expected.
4. `test_inference_is_deterministic_on_real_segment` — **real segment,
   real model**, called twice, identical score both times.
5. `test_happy_path_real_segment_real_model_produces_valid_signal` — the
   primary happy-path test; **the model is not mocked.**
6. `test_missing_artifact_directory_is_unavailable`
7. `test_corrupt_model_file_is_unavailable`
8. `test_invalid_manifest_json_is_unavailable`
9. `test_incompatible_experiment_id_is_unavailable`
10. `test_feature_schema_mismatch_is_unavailable`
11. `test_feature_names_reordered_is_unavailable` — same columns,
    different order, still rejected (the second, model-side check from
    Section 4).
12. `test_missing_slope_deg_is_unavailable`
13. `test_missing_elevation_m_is_unavailable`
14. `test_as_of_date_before_proxy_validity_window_is_unavailable`
15. `test_ml_disabled_returns_unavailable_without_loading_artifact` — the
    loader function is monkeypatched to raise if called at all, proving
    the disabled path short-circuits before touching any file.
16. `test_ml_disabled_does_not_affect_existing_risk_score` — the isolation
    guarantee, checked both with ML disabled and enabled.
17. `test_ml_risk_signal_never_raises_for_any_failure_mode` — sweeps every
    failure mode above and confirms none of them raises.

All artifact-mutation tests (`missing`/`corrupt`/`invalid manifest`/
`schema mismatch`/`reordered`) copy the real `v2_17_feature/` directory
into a `tmp_path` fixture and corrupt the copy — **the real artifact files
on disk are never touched.**

---

## 9. Test suite results

```
Before this part (Part 15A baseline):  627 passed
New tests added:                        17 (test_ml_risk_signal.py)
                                       + 1 (test_ml_segment_year_dataset.py,
                                            companion isolation check)
                                       - 0 removed
After this part:                       645 passed
```

Full run:

```
645 passed, 12 warnings in 152.34s (0:02:32)
```

The single pre-existing test that needed updating
(`test_no_ml_module_is_imported_by_production_code`) is explained in
Section 7 — it encoded an invariant ("zero ML code reachable from
`app/core`") that this part's own instructions explicitly supersede for
one named, isolated file; the test was narrowed to that exact exception
and a new test was added to keep enforcing the real isolation boundary
(not wired into risk/routing/reroute/hazard, not reachable from the API).
No other existing test was modified, and none of `risk_engine.py`,
`routing_engine.py`, `reroute_service.py`, `hazard_state.py`, `app/api/*`,
`app/simulation/*`, or the frontend were touched — confirmed via `git
status`.

---

## 10. Confirmation: production behavior is unchanged

- **Risk score:** Section 6's table shows every sampled segment's
  `assess_segment_risk().risk_score` is identical before and after
  calling the ML service, for both a successful and a would-be-enabled
  call.
- **Routing/rerouting/hazard behavior:** untouched — `routing_engine.py`,
  `reroute_service.py`, and `hazard_state.py` were not opened for editing
  in this part (confirmed via `git status`), and
  `test_ml_risk_signal_module_is_not_wired_into_risk_or_routing_engines`
  asserts, by static analysis of the actual source, that none of those
  three modules imports `ml_risk_signal` and that `ml_risk_signal.py`
  imports none of them.
- **API/frontend:** no route file, `app/main.py`, or any frontend file was
  modified or references `ml_risk_signal`/`MLRiskSignal` anywhere.
- **Default-off guarantee:** `ML_RISK_ENABLED = False` is the shipped
  default; `test_ml_disabled_returns_unavailable_without_loading_artifact`
  proves the service does not even attempt to read a file when disabled.

---

## Next steps (not started — separate approval required)

Per this part's explicit boundary, nothing here wires the service into
any API route, routing cost, or the frontend. That remains Part 15's own
staged plan (15C: API exposure, 15D: routing display-only interaction,
15E: UI block) — each requiring its own explicit review before starting,
exactly as this part's instructions require.
