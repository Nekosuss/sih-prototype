# Part 15D — ML Risk Signal Dashboard Integration

**Status:** Display-only. Backend `risk_engine.py`, `routing_engine.py`,
`reroute_service.py`, `hazard_state.py`, `ml_risk_signal.py`, and the
`GET /segments/{id}/ml-risk` endpoint implementation are all untouched.
Only frontend files changed, plus one new frontend utility module.

---

## 1. Where ML appears

**`SegmentDetailPanel.jsx`** — the existing click-to-inspect panel (Part
11), opened by clicking any real road segment on the map. It already
showed hazard-zonation, rainfall, and the authoritative Part 5 risk score.
This part adds one new, clearly-separated section beneath the existing
content:

```
AUTHORITATIVE · CURRENT SEGMENT RISK
0.08  [LOW]
...methodology note (unchanged)...
─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
ADVISORY · ML RISK SIGNAL
0.018  [LOW]                          <- neutral outlined badge, not colored
Model: part15a_segment_year_v2_17feature · Status: Advisory
Prototype ML ranking signal; not a calibrated probability and not
used to determine routing or safety decisions.
```

No other component was touched. `MapView.jsx`, `App.jsx`,
`RouteSummary.jsx`, `RiskBreakdown.jsx`, `RouteComparison.jsx`, and every
other existing component are byte-for-byte unchanged (confirmed via `git
status` — see Section 9). This is a single addition to a single existing
panel, not a new dashboard.

**Section 11 (optional aggregate indicator) was deliberately not added.**
The task marked it optional ("If the existing dashboard has a natural
place for it, you may add...") and explicitly warned against regional
ML KPIs. Adding a new summary surface would be scope beyond "selected
segment inspection," so it was skipped to keep this part minimal and
reversible.

---

## 2. API used

`GET /segments/{segment_id}/ml-risk` (Part 15C, unmodified) via a new
one-line client function:

```js
// frontend/src/api/client.js
export function getSegmentMlRisk(segmentId) {
  return getJson(`/segments/${segmentId}/ml-risk`);
}
```

Fetched exactly once per segment selection, in `SegmentDetailPanel.jsx`'s
own `useEffect` keyed on `segmentId` — **not polled**, and **fully
independent** of the existing hazard/weather `Promise.all` fetch in the
same component (a separate `useEffect`, separate state
`ml`/`mlLoading`/`mlError`). This isolation means a slow, failed, or
unavailable ML response can never delay or break the authoritative
hazard/weather/risk content, and vice versa.

---

## 3. Terminology

The panel never says "probability," "chance," or "likelihood" as a
positive claim about the score. Exact copy used:

- Section label: **"Advisory · ML Risk Signal"**
- Score is shown as a plain decimal (`0.018`), not a percentage.
- A small, neutral, outlined tier badge (`Low` / `Moderate` / `Elevated`)
  sits next to it — see Section 8 for why this is intentionally *not* the
  colored `.risk-pill` used for the authoritative score.
- Fixed methodology line, always shown: *"Prototype ML ranking signal;
  not a calibrated probability and not used to determine routing or
  safety decisions."* — this text comes straight from the task's own
  required wording and is never paraphrased away.
- `Status: Advisory` is shown explicitly next to the model version.

Verified programmatically (not just by inspection): a real Playwright
browser session's full page text was scanned for `"probability of
landslide"`, `"% chance"`, `"percentage chance"`, `"chance of landslide"`,
and `"likelihood percentage"` — none present, in either the ML-disabled
or ML-enabled state (Section 7).

---

## 4. Authoritative vs. advisory — how the hierarchy is made explicit

1. **Labeling:** the existing risk block now has an added
   `"Authoritative · Current Segment Risk"` label directly above it (new,
   one line); the new ML block is headed `"Advisory · ML Risk Signal"`.
   Both use the same small-caps label style already used elsewhere in this
   panel (`.compare-card__label`) — consistent, not a new visual
   vocabulary.
2. **Visual weight:** the authoritative score keeps its existing colored
   `.risk-pill` (green/amber/red/dark-red by `risk_level`) — unchanged.
   The ML tier badge is a **plain outlined badge in neutral ink**, with no
   fill color tied to the score's magnitude (`.ml-advisory__tier` in
   `styles/index.css`) — deliberately never color-coded the way the
   authoritative pill is, so it cannot visually read as "equally
   official."
3. **Ordering and separation:** the ML block sits *after* the
   authoritative methodology note, behind its own dashed separator
   (`.ml-advisory`, matching the existing dashed-separator convention
   already used for `.methodology-note`) — never interleaved with or
   placed above the authoritative content.
4. **Explicit text:** the authoritative section's methodology note was
   extended with one added sentence: *"This score, its weights, and
   thresholds are the sole basis for routing, thresholds, and
   PROCEED/REROUTE/SUSPEND decisions."* — stating the hierarchy in words,
   not just via layout.

See the screenshots in Section 7 for how this renders.

---

## 5. Disabled behavior (`ML_RISK_ENABLED = False`, the shipped default)

The panel calls the endpoint regardless (it always returns `200`), and
renders based on `available`:

```
ADVISORY · ML RISK SIGNAL
ML advisory signal unavailable
Prototype ML ranking signal; not a calibrated probability and not
used to determine routing or safety decisions.
```

No score, no `0`, no "n/a" number — the muted, italic
`"ML advisory signal unavailable"` line (`.ml-advisory__muted`) is the
entire visible state. The specific backend `reason` string (e.g. "ML risk
signal disabled by configuration...") is attached only as a native
`title` tooltip attribute, never shown as primary text — keeping the
default state clean per the task's explicit instruction, while still
letting a developer inspect the real reason on hover if needed.

---

## 6. Error / loading behavior

| State | Rendered |
|---|---|
| Fetching | `Loading ML signal…` (muted, same style as "unavailable") |
| `available: false` (any backend reason — disabled, artifact missing, unsupported segment, etc.) | `ML advisory signal unavailable` |
| Request itself fails (network error, unexpected 5xx) | Same `ML advisory signal unavailable` message — `mlError` is handled identically to `available: false` in the rendered text, so a caller can't tell "the service said no" from "the request failed" without opening dev tools, which is the right level of detail for an operations dashboard |
| Success | Score, tier badge, model version, "Advisory" status |

Verified with a real, forced network failure (Playwright's request
interception aborted only the `ml-risk` call, leaving every other request
— network, hazard layers, weather, risk — hitting the real backend
normally): the authoritative hazard/weather/risk content rendered fully
and correctly, the ML section showed the clean unavailable message, and
no error boundary, blank panel, or broken map interaction occurred.
Screenshot: `screenshots_part15d/03_ml_api_failure_isolated.png`.

---

## 7. Browser verification (real Playwright session, real backend, real model)

No frontend testing framework or Playwright/JS test runner is currently
installed in this project (`frontend/package.json` has no test script and
no `devDependencies` beyond Vite/the React plugin) — per this task's own
conditional instructions ("Add frontend tests **if** the project currently
has a frontend testing framework" / "**If** Playwright is already
available, use it"), neither condition was met for adding a permanent JS
test suite, and installing a new testing framework was treated as out of
scope for a display-only part. A Playwright **Python** installation was
already present in this environment, so real, live, browser-based
verification was performed instead of relying on code review alone — both
the real FastAPI backend (`uvicorn`) and the real Vite dev server were run
together, exactly as a user would.

**Verified, live, with a real browser, against the checklist:**

1. **Dashboard loads with ML disabled** — confirmed (`ML_RISK_ENABLED`
   left at its shipped default, `False`, on disk the entire time).
2. **Existing route/risk UI works normally** — a real route
   (Guwahati → Tawang) was calculated via the actual UI controls;
   `RouteSummary`/`RiskBreakdown` rendered their normal real values
   (distance 501.14 km, ETA 11h 43m, risk 0.39, HIGH).
3. **Select a real segment** — a real corridor segment (`seg_22832893_0`,
   "Bhalukpong-Doimara") was clicked directly on the map.
4. **ML unavailable state appears cleanly** — confirmed: `"ML advisory
   signal unavailable"`, no `0`, no crash.
5. **Enable ML** — done via an in-process `app.config.ML_RISK_ENABLED =
   True` on a temporary server instance; **`config.py` on disk was never
   modified** (confirmed via `git diff --stat` before and after: 41
   insertions, unchanged, matching Part 15B's original addition exactly).
6. **Select a real segment (ML enabled)** — same segment.
7. **Real ML signal appears** — `0.018`, tier `LOW`, from the real saved
   v2 Random Forest (no mocking).
8. **Model version appears** — `part15a_segment_year_v2_17feature`.
9. **Score appears as a signal, not a probability** — confirmed
   programmatically: the full rendered page text contains no positive
   probability/chance/likelihood claim, in either state.
10. **API failure does not break segment details** — confirmed via a
    forced-abort test (Section 6).
11. **Existing route and risk score remain unchanged** — confirmed
    programmatically: the "Route Risk" panel's score was read via a
    Playwright locator *before* and *after* the segment/ML interaction in
    both the disabled and enabled runs — identical (`0.39` both times, in
    both runs).
12. **ML score does not change route colors** — trivially true by code
    inspection (`MapView.jsx` was never touched, and reads nothing from
    `ml`/`ml-risk`), and visually confirmed: the route polyline color/style
    in the enabled and disabled screenshots is identical.
13. **ML score does not change PROCEED/REROUTE/SUSPEND** — the decision
    banner (`"Proceed — Route is currently safe to proceed"`) is identical
    in both screenshots; it is derived entirely from `riskAwareResult`/
    `hazardDecision` (`utils/risk.js::deriveDecision`), neither of which
    this part touched or which reads anything ML-related.

**Screenshots** (`backend/app/data/ml/screenshots_part15d/`):

- `01_ml_disabled_segment_detail.png` — full dashboard, ML disabled,
  segment panel open, showing the clean "unavailable" advisory state.
- `02_ml_enabled_segment_detail.png` — same segment, ML enabled, showing
  the real score/tier/model version, with the authoritative score and
  route summary numbers identical to the disabled screenshot.
- `03_ml_api_failure_isolated.png` — ML request forcibly failed；
  authoritative content intact, advisory section shows a clean
  unavailable state.

Zero browser console errors were observed in any of the three live runs
(disabled, enabled, forced-failure).

---

## 8. Visual design notes

- Reused existing classes wherever the visual need matched exactly:
  `.compare-card__label` (section labels), `.methodology-note`'s dashed-
  separator convention (new `.ml-advisory` mirrors it), `.risk-headline`'s
  layout shape (new `.ml-advisory__row`/`.ml-advisory__score` mirror it at
  a slightly smaller scale to read as secondary).
- The only genuinely new visual element is `.ml-advisory__tier` — a small
  outlined, uncolored badge. No gauge, meter, chart, gradient, or
  animation was added anywhere.
- No new color tokens were introduced; every color used
  (`--color-ink`, `--color-ink-soft`, `--color-ink-faint`,
  `--color-border`, `--color-border-strong`, `--font-mono`) already
  existed in `styles/index.css` before this part.

---

## 9. Files changed

| File | Change |
|---|---|
| `frontend/src/api/client.js` | **Additive.** One new function, `getSegmentMlRisk()`. |
| `frontend/src/utils/mlRisk.js` | **New.** `mlRankingTier()` (display-only bucketing, explicitly documented as not a backend classification) and `humanizeMlUnavailableReason()` (tooltip text only). |
| `frontend/src/components/SegmentDetailPanel/SegmentDetailPanel.jsx` | **Modified.** New independent `ml`/`mlLoading`/`mlError` state + `useEffect`; new "Authoritative" label above the existing risk block; new "Advisory · ML Risk Signal" block after the existing methodology note. Every existing line of logic/markup for hazard/weather/risk is otherwise unchanged. |
| `frontend/src/styles/index.css` | **Additive.** One new CSS block (`.ml-advisory` and its children), appended after `.methodology-note`. No existing rule was edited. |

Backend files changed in this part: **none.** (`git status backend/`
before and after this part is identical except for the new documentation
file and screenshots directory this part adds.)

Not touched: `App.jsx`, `MapView.jsx`, `RouteSummary.jsx`,
`RiskBreakdown.jsx`, `RouteComparison.jsx`, `HazardControl.jsx`,
`VehiclePanel.jsx`, `FieldReportPanel.jsx`, `utils/risk.js`, and every
backend file under `app/core`, `app/api`, `app/simulation`.

---

## 10. Regression confirmation

- **Backend:** full suite re-run after this part (frontend-only changes)
  — **657/657 passing**, identical to the Part 15C baseline (no backend
  file changed, so this is an exact-repeat confirmation, not a new count).
- **Frontend build:** `npm run build` succeeds cleanly (94 modules
  transformed, no errors/warnings beyond normal bundle-size output).
- **Route calculation, risk calculation, hazard simulation, vehicle
  simulation, field reporting:** none of their backing code was modified
  in this part (confirmed via `git status`), and the live browser session
  exercised route calculation and the decision banner directly (Section
  7, items 2 and 11–13) without any deviation from pre-existing behavior.
  Hazard simulation, vehicle simulation, and field reporting were not
  separately re-exercised live in this part (their components/backends
  are untouched here and already have their own passing backend/API
  regression tests re-confirmed by the 657/657 run above).

---

## 11. Explicit statement (required by this task)

**The ML risk signal shown in the dashboard has ZERO effect on routing or
safety decisions.** It is fetched only for display, in one isolated
component, from one unmodified read-only endpoint. Nothing in this part
touched `risk_engine.py`, `routing_engine.py`, `reroute_service.py`,
`hazard_state.py`, `ml_risk_signal.py`, the `GET /segments/{id}/ml-risk`
implementation, `MapView.jsx`'s route rendering, or
`utils/risk.js::deriveDecision`'s PROCEED/REROUTE/SUSPEND logic. Verified
live, not just by code inspection: the authoritative risk score, route
summary, route color, and decision banner were byte-for-byte identical
whether ML was enabled or disabled, before or after every ML interaction
performed in Section 7.
