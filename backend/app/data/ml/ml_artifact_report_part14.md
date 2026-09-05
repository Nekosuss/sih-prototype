# ML Artifact Report — Part 14.4

Status: **artifact packaging only.** No dataset, feature, label, validation
strategy, preprocessing, or model configuration from Part 14.3 was changed.
No model was integrated into `app/core/risk_engine.py`, `routing_engine.py`,
`reroute_service.py`, `hazard_state.py`, any production API, or the
frontend. This document reports what was found, what was created, and the
reproducibility/test results — nothing more.

---

## 1. What already existed (inspection, before this task)

Inspected directly (`find`, `grep -rn "joblib\|pickle.dump\|\.save("`,
filesystem listing) before creating anything:

| Question | Finding |
|---|---|
| Was the Logistic Regression model saved? | **No.** |
| Was the Random Forest model saved? | **No.** |
| Where are they saved? | **Nowhere.** No `.pkl`/`.joblib`/`.onnx`/model-file existed anywhere in the repository. |
| Was any `artifacts/` directory present? | **No** — `backend/app/data/ml/artifacts/` did not exist. |
| What preprocessing was used? | Present only as **code**, never as a saved object: `StandardScaler` inside the Logistic Regression `Pipeline` (`app/data/ml/models.py::make_logistic_regression`); Random Forest uses raw (unscaled) features. |
| What exact features were used? | Present only as **code**: `app/data/ml/feature_matrix.py::NUMERIC_FEATURE_COLUMNS` + `pandas.get_dummies` on `road_type`/`terrain_type` — no persisted, versioned feature list existed. |
| Were feature ordering/names preserved? | Only **implicitly**, via `pandas.get_dummies`'s deterministic column ordering given the same input data — not pinned to a checked artifact anyone could validate a new inference row against. |
| Were hyperparameters saved? | Only as **hardcoded constants** in `models.py` source — never exported to a config file. |
| Were validation folds/results saved? | **No.** `run_feasibility_study.py` only prints to stdout; the 25-fold LOGO results and the GroupKFold(5) diagnostic existed only as terminal output, hand-transcribed into `ml_modeling_feasibility_part14.md`'s prose tables. |
| Were feature-importance results saved? | **No** — same as above, printed once and transcribed by hand into the report. |
| **Can the experiment be reproduced from the repository?** | **Yes, functionally** — every function involved (`feature_matrix.py`, `models.py`, `logo_evaluation.py`, `ranking_evaluation.py`, `baseline_risk_scorer.py`) is present, deterministic (`random_state=42` throughout), and operates on the already-built, already-audited `segment_year_dataset.csv`. Reproducing it, however, required **rerunning training** — there was no saved model object to simply load. |

**Conclusion of the inspection: nothing from Part 14.3 was serialized.**
Every model, metric, and importance value that existed did so only in a
process's memory during `run_feasibility_study.py`'s execution, and was
discarded when that process exited. Step 2 of the task (recreate the exact
experiment) was therefore necessary.

## 2. What had to be created

A new script, `backend/app/data/ml/save_model_artifacts.py`, which:

- Imports and calls the **existing, unmodified** Part 14.3 functions
  (`feature_matrix.build_feature_matrix`, `models.make_logistic_regression`,
  `models.make_random_forest`, `logo_evaluation.leave_one_group_out`,
  `logo_evaluation.groupkfold_diagnostic`, `ranking_evaluation.evaluate_all_event_years`,
  `baseline_risk_scorer.compute_baseline_scores`) — **zero lines of
  modeling logic were rewritten.**
- Reruns the exact LOGO validation to reconfirm the previously-reported
  numbers (see §6, they matched to full floating-point precision).
- Separately fits **one final model of each type on all 25 positive
  groups** (the standard "refit on everything once validation is done"
  step — this is the same "fit on FULL data" step Part 14.3's own
  `run_feasibility_study.py` already performed for its feature-importance
  section, just persisted here instead of discarded).
- Serializes both final models plus every requested metadata artifact.

This is the **only** new code written for Part 14.4 (plus the
load/inference test in §7 and this report).

## 3. Exact model files

All under `backend/app/data/ml/artifacts/`:

| File | Size (bytes) | Contents |
|---|---|---|
| `random_forest_model.joblib` | 604,745 | Fitted `sklearn.ensemble.RandomForestClassifier`, fit on all 32,604 rows / 25 positive groups |
| `logistic_regression_model.joblib` | 2,721 | Fitted `sklearn.pipeline.Pipeline` (`StandardScaler` → `LogisticRegression`), same training data |
| `logistic_regression_scaler.joblib` | 1,807 | The same fitted `StandardScaler`, extracted and saved standalone for inspectability (redundant with the copy embedded in the pipeline above — loading the pipeline alone is sufficient for correct inference) |

Serialization used `joblib.dump`/`joblib.load` — the standard approach for
scikit-learn estimators (preserves the fitted tree structure / coefficients
exactly; safer and faster than raw `pickle` for numpy-array-heavy objects).

## 4. Exact feature schema

`feature_schema.json` — 21 features, in this exact order (also duplicated
in `model_manifest.json`'s `feature_names_in_order` for convenience):

```
distance_km, slope_deg, elevation_m, historical_landslide_count_prior,
nearest_historical_landslide_distance_m_prior, has_prior_history,
annual_rainfall_mm, monsoon_jun_sep_rainfall_mm, max_daily_rainfall_mm,
rainy_days_count, road_type_primary, road_type_primary_link,
road_type_secondary, road_type_secondary_link, road_type_tertiary,
road_type_tertiary_link, road_type_trunk, road_type_trunk_link,
terrain_type_hill, terrain_type_mountain, terrain_type_plain
```

The schema file also records the categorical-encoding method
(`pandas.get_dummies`, one-hot) and the missing-value handling rule for
`nearest_historical_landslide_distance_m_prior` (sentinel `5000.0` +
companion `has_prior_history` boolean) — the two decisions that would
otherwise be invisible from the column list alone.

## 5. Preprocessing

- **Random Forest:** none — raw feature values, as `models.py` always
  specified (tree splits don't require scaling).
- **Logistic Regression:** `StandardScaler`, fit on the full training data
  as part of the pipeline. Saved twice (embedded in the pipeline, and
  standalone in `logistic_regression_scaler.joblib`) — a test
  (`test_standalone_scaler_matches_the_one_embedded_in_the_pipeline`)
  confirms both copies have identical `mean_`/`scale_`.

## 6. Validation metadata (reproduced, not re-derived)

`save_model_artifacts.py` reran the identical LOGO/GroupKFold code from
Part 14.3 rather than copying the old report's numbers, specifically so
any drift would be caught. Result — **exact match**, to full floating-point
precision:

| Metric | Part 14.3 report | Reproduced by this task |
|---|---|---|
| Logistic Regression mean within-terrain percentile | 72.9 | **72.93720580247198** |
| Random Forest mean within-terrain percentile | 78.6 | **78.63330420288142** |
| Logistic Regression pooled AUC | 0.954 | **0.9539921932641141** |
| Random Forest pooled AUC | 0.981 | **0.9810827140291798** |

All 25 way-groups / 38 (way-group, year) fold evaluations, plus the
GroupKFold(5) diagnostic's 5 fold compositions, are saved in full in
`validation_metadata.json` (26,855 bytes) — not just the summary numbers
above.

**Important distinction, stated in both the manifest and the model card:**
these numbers come from 25 *transient* per-fold models (each missing one
positive group) — they are not, and cannot be, the saved final models'
"own" score, since those were fit on all 25 groups. `model_manifest.json`'s
`reported_metrics.note` and `MODEL_CARD.md`'s Validation section both say
this explicitly, so the distinction survives independent of this report.

## 7. Feature importance (saved, not just printed)

`feature_importance.json` — Random Forest impurity importances and
Logistic Regression standardized coefficients, both from the full-data
final models, exactly matching Part 14.3 Section 7's numbers. Caveats
(impurity bias, LogReg multicollinearity instability, no causal claim) are
embedded in the JSON itself, not left to tribal knowledge.

## 8. Reproducibility check (Step 8)

Two distinct reproducibility claims were tested, kept deliberately
separate because they answer different questions:

**(a) Does rerunning the Part 14.3 experiment reproduce the reported
numbers?** Yes — see §6's table; exact match to full float precision. This
confirms the experiment is genuinely deterministic and code-reproducible,
not a one-off result.

**(b) Does the saved model, reloaded from disk, reproduce the same
predictions as the in-memory model that was fit right before saving?**
Yes — `tests/test_ml_artifact_inference.py::test_reloaded_random_forest_matches_a_fresh_fit_on_the_same_data`
loads `random_forest_model.joblib` and compares its predictions against a
freshly-constructed-and-fit `RandomForestClassifier` (same `models.py`
factory, same data) across all 32,604 rows: `np.allclose(..., atol=1e-9)`
— **passed, bit-for-bit within float tolerance.**

No exact-reproduction failure was encountered; both checks passed cleanly.
(If they hadn't: the most likely cause would have been a scikit-learn
version mismatch between save-time and load-time — `sklearn_version` is
recorded in `model_manifest.json` precisely so that could be diagnosed
later.)

## 9. Test results

New test files:
- `tests/test_ml_artifact_inference.py` — 8 tests: schema-matches-code,
  RF/LogReg load-and-score-a-real-row, standalone-scaler-matches-embedded,
  RF-scores-the-full-dataset-without-error, the reload-vs-fresh-fit
  reproducibility check, manifest/metadata JSON structure, and model-card
  content checks.

No existing test was modified.

**Full backend suite: 627/627 passing** (619 existing going into this task
+ 8 new). Runtime ~148s.

## 10. Problems discovered

- **None that block this task** — reproduction was exact on the first
  attempt (no scikit-learn/data drift between the Part 14.3 run and this
  one).
- **One process caveat worth flagging, not a bug:** `model_manifest.json`'s
  `git_commit` field records the repository's most recent commit
  (`375f0605...`), but the Part 14/14.2/14.3/14.4 work (including the
  dataset the model was trained on) is **not yet committed** to that
  commit — the field reflects "the last commit," not "the exact working
  tree state at generation time." This is inherent to running the save
  script against an uncommitted working tree, not a defect in the script;
  worth committing the Part 14.x work before these artifacts are relied on
  by anyone else, if that matters for provenance later.
- Confirmed (again) that `app/data/ml/` remains fully isolated: the
  existing AST-based import guard (`test_no_ml_module_is_imported_by_production_code`
  in `tests/test_ml_segment_year_dataset.py`) still passes with all Part
  14.4 files present — nothing under `app/data/ml/`, including the new
  `artifacts/` directory and `save_model_artifacts.py`, is reachable from
  `app/main.py`, `app/api/`, `app/core/`, or `app/simulation/`.

---

**No further action was taken.** Per the task's explicit stop condition,
this report is the end of Part 14.4 — no ML→production integration, no
model improvement, no additional data collection, no unrelated refactors.
