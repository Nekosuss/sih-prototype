# Part 15C — ML Risk Signal API

**Status:** API exposure only. `risk_engine.py`, `routing_engine.py`,
`reroute_service.py`, `hazard_state.py`, and the frontend are untouched.
This endpoint only reads and returns the isolated Part 15B ML signal — it
computes no new risk score, and nothing it does affects routing, hazard
processing, or rerouting. See Section 6 for the explicit statement this
task requires.

---

## 1. Endpoint

```
GET /segments/{segment_id}/ml-risk
```

Added to `app/api/routes_network.py`, alongside the existing segment-scoped
endpoints (`GET /segments/{id}/risk`, `GET /segments/{id}/risk-aware`),
following their exact conventions: same lookup pattern
(`state_store.get_segment(segment_id)`), same 404 shape and message for an
unknown id, same `response_model=` usage.

### Request

No request body. `segment_id` is a path parameter — any id from
`GET /segments`.

```
GET /segments/seg_44736742_0/ml-risk
```

### Response — `MLRiskSignal` (`app/models/ml_risk.py`)

```json
{
  "available": true,
  "score": 0.8273,
  "model_version": "part15a_segment_year_v2_17feature",
  "feature_schema_version": "00905a522d1a60ac",
  "reason": "ok",
  "methodology_note": "Experimental ML ranking score from an uncalibrated prototype Random Forest (backend/app/data/ml/artifacts/v2_17_feature/MODEL_CARD.md). This is NOT a calibrated probability of a landslide occurring, and is NOT used anywhere in the production risk score, routing cost, hard unsafe threshold, or PROCEED/REROUTE/SUSPEND decisions -- see backend/app/data/ml/ml_integration_design_part15.md."
}
```

| Field | Type | Meaning |
|---|---|---|
| `available` | bool | Whether a signal was produced for this request. `false` for any reason (disabled, artifact unavailable, unusable features, inference failure) — see Section 4. |
| `score` | float \| null | The model's raw `predict_proba()` positive-class output, bounded [0,1]. **An "ML risk signal" / "ML ranking score" — never a probability.** `null` whenever `available` is `false`. |
| `model_version` | string \| null | The loaded artifact's `model_manifest.json::experiment_id` — currently `"part15a_segment_year_v2_17feature"`. `null` if the artifact never loaded (e.g. disabled). |
| `feature_schema_version` | string \| null | A 16-character fingerprint of the artifact's ordered 17-feature schema — lets a caller detect if a future artifact swap changed the schema. |
| `reason` | string | Always populated. `"ok"` on success; otherwise a specific, plain-language explanation (Section 4). |
| `methodology_note` | string | Fixed disclaimer text, always present, always says "NOT a calibrated probability" and "NOT used anywhere in the production risk score, routing cost, ... decisions." |

**Status codes:**

| Code | When |
|---|---|
| `200` | Segment exists — `available` may be `true` or `false` inside the body. An unavailable ML signal is a normal, structured outcome, not an error (same convention as `RiskAwareRouteResult.outcome == "no_safe_route_available"` in `routes_routing.py`, which is also a 200). |
| `404` | `segment_id` does not exist — `{"detail": "Unknown segment: <id>"}`, identical shape to every other `/segments/{id}/*` endpoint. |

---

## 2. Score terminology — enforced, not just documented

The response body never uses "probability of landslide," "calibrated
probability," or "percentage/likelihood chance" as a positive claim.
`methodology_note` is fixed text carried straight from
`MLRiskSignal`/`ml_risk_signal.py` (Part 15B) — this endpoint does not
rewrite, summarize, or relabel it. Enforced by
`test_ml_risk_endpoint_never_asserts_score_is_a_probability`, which checks
the full response body for any positive probability claim and requires
that the only permitted mention of "probability" is the negation ("NOT a
calibrated probability") — the same convention already enforced elsewhere
in this codebase for `/routes/calculate-risk-aware`'s `reasons`
(`test_calculate_risk_aware_route`).

---

## 3. ML disabled behavior (the default)

`ML_RISK_ENABLED = False` ships as the default (`app/config.py`, Part
15B). With it `False`:

```
GET /segments/seg_44736742_0/ml-risk
```

```json
{
  "available": false,
  "score": null,
  "model_version": null,
  "feature_schema_version": null,
  "reason": "ML risk signal disabled by configuration (ML_RISK_ENABLED=False)",
  "methodology_note": "..."
}
```

The endpoint calls `ml_risk_signal.get_ml_risk_signal(segment)`, which
checks `ML_RISK_ENABLED` **first**, before touching any file — no artifact
is loaded, no model is deserialized, no inference runs. Verified directly
(not just by absence of a crash) by
`test_ml_disabled_returns_unavailable_without_loading_artifact` (Part
15B), which monkeypatches the loader to raise if it's ever called and
confirms it never is.

---

## 4. Failure behavior

Every failure mode resolves to a `200` response with `available: false`
and a specific `reason` — the endpoint never returns a 5xx for an ML
problem, and the only `4xx` it can return is a `404` for a genuinely
unknown `segment_id`:

| Scenario | HTTP status | `available` | `reason` (example) |
|---|---|---|---|
| Unknown segment | `404` | — | `{"detail": "Unknown segment: ..."}` |
| ML disabled | `200` | `false` | "ML risk signal disabled by configuration..." |
| Artifact directory missing | `200` | `false` | "ML model artifact unavailable or invalid (see server logs)" |
| Corrupt `.joblib` file | `200` | `false` | same as above |
| Manifest/schema mismatch or incompatible artifact | `200` | `false` | same as above (specific mismatch logged server-side, not exposed in the response — avoids leaking artifact internals to a caller) |
| Segment missing `slope_deg`/`elevation_m` | `200` | `false` | "segment '...': slope_deg unavailable (no DEM coverage)" |
| Model inference exception | `200` | `false` | "model inference raised an exception (see server logs)" |
| Non-finite/out-of-range model output | `200` | `false` | "model returned a non-finite or out-of-range score (...)" |

All of this behavior lives entirely in `core/ml_risk_signal.py` (Part
15B) — the API layer added in this part contributes **no additional
error handling of its own** beyond the standard segment-lookup 404,
because `get_ml_risk_signal()` already never raises. This is deliberate:
duplicating failure handling at the API layer would risk it drifting from
the service's own guarantees.

**Most importantly:** a failure in the ML path cannot propagate anywhere
else. `get_segment_ml_risk()` in `routes_network.py` calls exactly one
function (`get_ml_risk_signal`) and returns its result — it does not call
`risk_engine.py`, `routing_engine.py`, `reroute_service.py`, or
`hazard_state.py`, so nothing about route calculation, risk calculation,
rerouting, or hazard processing can be affected by any ML failure mode.
Confirmed programmatically in Section 5.

---

## 5. Regression protection — verified programmatically

Two new tests calculate `/routes/calculate-risk-aware` for
Guwahati→Tawang, then call `GET /segments/{id}/ml-risk` for every segment
on the fastest route (up to 20), then recalculate the same route and
assert **every** field is unchanged:

- `test_existing_route_calculation_unaffected_by_ml_risk_endpoint_disabled`
  — ML disabled (the shipped default).
- `test_existing_route_calculation_unaffected_by_ml_risk_endpoint_enabled`
  — ML enabled, real model, real inference actually running in between the
  two route calculations.

Both assert identical: `outcome`, `fastest_route.segment_ids`,
`total_distance_km`, `estimated_travel_time_min`,
`fastest_route_risk.aggregate_risk_score`,
`fastest_route_segment_risks`, and (when present)
`recommended_route.segment_ids` / `recommended_route_risk`.

A third test,
`test_existing_segment_risk_endpoints_unaffected_by_ml_risk_endpoint`,
confirms `GET /segments/{id}/risk` and `GET /segments/{id}/risk-aware`
return byte-identical bodies before and after calling the new ML endpoint
for the same segment.

---

## 6. Explicit statement (required by this task)

**The ML risk signal exposed by `GET /segments/{id}/ml-risk` is purely
advisory. It currently has NO effect on:**

- the production `risk_score` / `risk_level` (`risk_engine.py`,
  unmodified),
- routing cost or path selection (`routing_engine.py`, unmodified),
- the hard unsafe threshold or any segment exclusion,
- route-change hysteresis,
- CONTINUE / REROUTE / SUSPEND decisions (`reroute_service.py`,
  unmodified),
- hazard state or hazard-driven closures (`hazard_state.py`, unmodified),
- vehicle behavior, or
- anything rendered in the frontend (untouched in this part).

This endpoint exists solely so the signal can be inspected over HTTP —
nothing in the application reads its response yet.

---

## 7. Files changed

| File | Change |
|---|---|
| `backend/app/api/routes_network.py` | **Modified, additive only.** New imports (`get_ml_risk_signal`, `MLRiskSignal`) and one new endpoint, `GET /segments/{id}/ml-risk`. No existing route in this file was changed. |
| `backend/tests/test_api.py` | **Modified, additive only.** 11 new tests appended in a new "Part 15C" section (Section 8 below). No existing test was changed. |
| `backend/tests/test_ml_segment_year_dataset.py` | **Modified.** Part 15B's guard test forbidding *any* API file from importing `ml_risk_signal` is superseded by this part's explicit instruction — narrowed to name `api/routes_network.py` as the one approved exception, and a new test (`test_ml_risk_api_endpoint_is_the_only_api_file_reaching_ml_risk_signal`) confirms it remains the *only* one. The permanent isolation boundary (risk_engine/routing_engine/reroute_service/hazard_state must never import `ml_risk_signal`, and vice versa) is unchanged and still separately enforced. |

`app/core/ml_risk_signal.py`, `app/models/ml_risk.py`, and
`app/config.py`'s ML section (all Part 15B) are unmodified by this part.

---

## 8. Tests added (`test_api.py`)

1. `test_ml_risk_endpoint_disabled_by_default_returns_clean_unavailable`
2. `test_ml_risk_endpoint_unknown_segment_returns_404`
3. `test_ml_risk_endpoint_enabled_real_segment_real_model` — the primary
   happy-path test; **not mocked**, real segment + real saved v2 model.
4. `test_ml_risk_endpoint_returns_model_version`
5. `test_ml_risk_endpoint_returns_feature_schema_version`
6. `test_ml_risk_endpoint_deterministic_across_repeated_requests`
7. `test_ml_risk_endpoint_artifact_unavailable_returns_clean_unavailable`
8. `test_ml_risk_endpoint_never_asserts_score_is_a_probability`
9. `test_existing_route_calculation_unaffected_by_ml_risk_endpoint_disabled`
10. `test_existing_route_calculation_unaffected_by_ml_risk_endpoint_enabled`
11. `test_existing_segment_risk_endpoints_unaffected_by_ml_risk_endpoint`

Plus 1 new test in `test_ml_segment_year_dataset.py`:
`test_ml_risk_api_endpoint_is_the_only_api_file_reaching_ml_risk_signal`.

---

## 9. Test suite results

```
Before this part (Part 15B baseline): 645 passed
New tests added:                       11 (test_api.py)
                                      +  1 (test_ml_segment_year_dataset.py)
                                      -  0 removed
After this part:                      657 passed
```

Full run:

```
657 passed, 12 warnings in 86.45s (0:01:26)
```

---

## 10. Confirmation: production risk/routing behavior is unchanged

- `git status` shows zero changes to `risk_engine.py`, `routing_engine.py`,
  `reroute_service.py`, `hazard_state.py`, any file under
  `app/simulation/`, or any frontend file.
- Section 5's regression tests programmatically confirm identical route
  segments, distance, travel time, aggregate/per-segment risk scores, and
  safety outcome before and after exercising the new endpoint, both with
  ML disabled and with ML enabled and actually run.
- `GET /segments/{id}/risk` and `GET /segments/{id}/risk-aware` return
  byte-identical bodies before and after calling the new endpoint.
- `ML_RISK_ENABLED = False` remains the shipped default; the endpoint
  never loads the model in that state.

## Next steps (not started — separate approval required)

Nothing calls this endpoint yet. Per Part 15's staged plan, connecting the
frontend to it (15E) and any eventual routing/risk interaction (15D,
narrowly scoped and explicitly gated per
`ml_integration_design_part15.md` Section 9) remain separate, future,
explicitly-approved parts.
