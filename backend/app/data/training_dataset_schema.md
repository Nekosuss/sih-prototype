# ML Training Dataset — Design Document (Part 4.7)

Status: **design only — no model has been trained, no labels have been fabricated,
no code in `core/`, `simulation/`, or the frontend was touched.**

This document is the output of Steps 1–7. It inventories what data actually
exists today, defines a defensible prediction target (or explains why one
cannot yet be defined), and specifies what must be collected before a
supervised model can be trained.

---

## STEP 1 — Current feature inventory

Source of truth: `backend/app/models/network.py` (`RoadSegment`),
`backend/app/data/osm_geojson_loader.py`, `backend/app/data/landslide_mapper.py`,
`backend/app/data/landslide_corridor_validation.py`, and
`backend/app/data/README.md` (which already carries a per-field real/derived/
not-assessed provenance table this section builds on).

| Feature | Source | Unit | Available? | Missingness | How it is calculated |
|---|---|---|---|---|---|
| `distance_km` | OSM (`guwahati_tawang_osm_corridor.geojson`) | km | ✅ Real | 0% | Haversine sum over real way coordinates (`osm_geojson_loader.py`) |
| `geometry` (LineString) | OSM | lat/lng pairs | ✅ Real | 0% | Direct from GeoJSON coordinates |
| `road_type` (highway class) | OSM `highway` tag | categorical | ✅ Real | 0% | Direct tag passthrough, restricted to the enum in `RoadType` |
| `name`, `ref` | OSM `name`/`ref` tags | text | ✅ Real | Partial — many ways untagged, esp. minor roads | Direct tag passthrough |
| `oneway`, `maxspeed` | OSM tags | text | ✅ Real | `maxspeed` present on only ~4% of ways (per README) | Direct tag passthrough |
| `assumed_speed_kph` | OSM `maxspeed` or highway-class default table | km/h | ✅ Real-or-assumed | 0% (always filled), but ~96% are assumptions, not measurements | `_parse_maxspeed_kph()` else `DEFAULT_SPEED_KPH[road_type]` |
| `estimated_travel_time_min` | Derived from the two rows above | minutes | ✅ Derived | 0% | `distance_km / assumed_speed_kph` |
| `elevation_m` | Open-Elevation API (SRTM), but only at 7 fixed demo towns | metres | ⚠️ Approximated | 0% populated, but not a per-segment measurement | Segment midpoint → nearest of 7 town points → that town's elevation (step function, `_nearest_reference()`) |
| `terrain_type` | Derived from `elevation_m` above | categorical (plain/hill/mountain) | ⚠️ Approximated | 0% populated, inherits the elevation approximation's error | Threshold: `<300m`=plain, `<1500m`=hill, else mountain |
| `slope_deg` | — | degrees | ❌ **Not implemented** | 100% — always `None` | No code path computes it anywhere in the repo (confirmed by full-repo search); `_build_segment()` hardcodes `slope_deg=None` |
| `landslide_susceptibility` | — | 0–1 score | ❌ **Not assessed** | 100% — always `0.0` | No hazard-zonation dataset available; left neutral rather than fabricated |
| `flood_susceptibility` | — | 0–1 score | ❌ **Not assessed** | 100% — always `0.0` | Same as above |
| `historical_landslide_count` | GSI inventory ⨯ OSM spatial join (`landslide_mapper.py`) | integer count | ✅ Real, but **not live-wired** | 0 for ~99% of segments (33 of 2,964 have ≥1); not merged into the running API by default — must read `derived/road_landslide_features.csv` directly | `groupby(matched_segment_id).size()` over MATCHED (≤500m) GSI records only. **Not deduplicated** — repeat reports near the same point each count separately |
| `nearest_landslide_distance_m` | Same pipeline | metres | ✅ Real, same wiring caveat | Null whenever count is 0 | Nearest-neighbor distance from `gpd.sjoin_nearest`, computed in an estimated UTM CRS |
| `district`, `movement_type` (GSI records only, not segment features) | `landslide_corridor_validation.py` | text | ⚠️ Best-effort | Depends on free-text quality of source description | Regex/keyword matching against hardcoded district/movement-type lists — explicitly not an authoritative GSI field |
| Event date / time of landslide | GSI raw CSV free text (`description_after_coordinates`, `raw_record`) | — | ⚠️ Inconsistent, unstructured | High — some records carry a full date (`"1 July 2016 at 12:20 hrs"`), some only a year (`"2008"`), some `"NA"` (no date at all) | **No parser exists.** Verified directly against `gsi_landslides_corridor.csv`: date format is not standardized and is sometimes entirely absent |
| Rainfall / weather (any form) | — | — | ❌ **Does not exist** | 100% | No rainfall/weather CSV, API integration, or model exists anywhere in the repo (verified by full-repo search for rain/weather/precip) |

Also confirmed while inventorying: `landslide_susceptibility`, `flood_susceptibility`,
`current_risk_score` (`== base_risk`, terrain-only formula), `status` (always
`"open"`) carry no information beyond what's already listed above and are not
independent features. `backend/app/models/weather.py`, `risk.py`,
`incident.py`, `weather_simulator.py`, `routes_weather.py` are comment-only
stub files — no `WeatherCondition`/`RiskScore`/`Incident` class exists in
code today, so there is nothing implemented there to build on or duplicate.

---

## STEP 2 — Defining the ML prediction target

**Candidate target statement:** *"probability that a road segment experiences
a landslide-related disruption within a short forward window, conditional on
recent rainfall/weather conditions."* This is the right target for
route-hazard assessment (it's actionable — a router can avoid a
currently-elevated-risk segment) and it is **not** the same thing as static
historical susceptibility.

**Why `historical_landslide_count > 0` is rejected as the target:**

1. **Severe class imbalance with no denominator.** Only 33 of 2,964 segments
   (1.1%) have any recorded event, and counts are absolute, not normalized by
   segment length, road age, or observation period.
2. **Reporting bias, not occurrence bias.** The GSI inventory is an
   opportunistic field-observation record (see `README.md`), not a
   systematic survey of every segment over a fixed period. A segment with
   `count = 0` may mean "no landslide occurred" or may simply mean "no one
   recorded one there." Training a classifier on this conflates *hazard*
   with *observation effort* — a segment near a town or a frequently
   inspected highway (e.g. NH13) is more likely to have a report regardless
   of true susceptibility.
3. **No temporal structure.** A static `count > 0` label cannot represent
   "disruption under given environmental conditions" — the stated goal
   requires knowing *when* a landslide happened relative to *what the
   weather was doing at the time*, which the current data does not capture
   (see Step 4/Step 9).

**Conclusion for this step:** the currently available data is **insufficient
to construct the intended (weather-conditional, event-level) target**, for
the reasons in Step 9. Per instructions, we stop at dataset design rather
than fabricate a label. The schema below is built so that once the missing
data (Step 10) is collected, no schema rework is needed — only population of
the additional columns.

---

## STEP 3 — Features vs. target (no leakage)

**FEATURES** (segment/event-time state, must be knowable *before* the event
being predicted):
- `slope_deg` *(REQUIRED, not yet available — Step 9)*
- `elevation_m` (currently approximated; a real per-segment DEM sample is a
  prerequisite for this to be trustworthy — see Step 9)
- `terrain_type`
- `road_type`, `distance_km`, `assumed_speed_kph` (road characteristics)
- `historical_landslide_count` **computed only from records strictly before
  the event/observation date being labeled** — see leakage rule below
- `nearest_historical_landslide_distance_m`, same temporal cutoff rule
- `rainfall_1d_mm` / `rainfall_3d_mm` / `rainfall_7d_mm` *(REQUIRED, not yet
  available — Step 4/9)*

**TARGET:**
- A binary (or graded) outcome: *did a landslide-related disruption occur on
  this segment within a defined forward window (e.g. the next 24–72h) of the
  observation date?* Constructed strictly from records **at or after** the
  observation date, never reused as a feature for that same observation.

**Leakage rule (explicit):** for any given training row keyed by
`(segment_id, observation_date)`, the row's `historical_landslide_count` /
`nearest_historical_landslide_distance_m` features must be computed using
**only** GSI records dated strictly before `observation_date`. The GSI
record used to *label* that row's outcome (if any occurs in the forward
window) must be excluded from that row's own feature computation. This
requires the currently-missing structured event dates (Step 9) — with the
dataset's current free-text/absent dates, this temporal split cannot be
implemented safely today, which is one more reason a defensible event-level
target cannot yet be built.

---

## STEP 4 — Weather data requirement

Minimum useful rainfall representation for this problem, based on standard
practice in landslide early-warning literature (antecedent + triggering
rainfall):

- **Daily rainfall (mm)** — the base unit; nothing coarser is useful for a
  "given environmental conditions" framing.
- **Rolling accumulations**: 1-day, 3-day, and 7-day trailing sums are the
  minimum set — antecedent soil saturation (3–7 day) is at least as
  predictive as the triggering day's total in most landslide-rainfall
  studies, and a 1-day figure alone would miss it.
- **Rainfall intensity** (mm/hr, if sub-daily data is obtainable) — useful
  but not minimum-required; daily totals are an acceptable first cut.

**Required spatial resolution:** at minimum, grid cells small enough to
distinguish West Kameng/Tawang district conditions from the Assam plains
portion of the corridor (elevation and rainfall regime differ sharply across
this route) — a gridded product on the order of 0.25° (~25km, e.g. IMD
gridded rainfall) is a usable minimum; finer (e.g. satellite-derived
0.1°/~10km, or station data near Bomdila/Dirang/Tawang/Bhalukpong) is
preferable given the corridor crosses steep elevation gradients over short
distances.

**Required temporal resolution:** daily, covering at minimum the full date
range spanned by the GSI inventory's events that do carry parseable dates
(spans at least 2008–2021 based on a spot check of `gsi_landslides_corridor.csv`)
plus enough recent history to be usable for real-time inference later.

No weather data is downloaded or fabricated in this step, per instructions.

---

## STEP 5 — Proposed training table

`training_dataset_schema` (documented shape — no file has been generated):

| Column | Type | Status |
|---|---|---|
| `segment_id` | string | ✅ obtainable now (`RoadSegment.id`) |
| `event_date` / `observation_date` | date | ⚠️ **REQUIRED** — GSI dates are free-text/inconsistent; needs a parsing/cleaning pass, and for negative examples a defined sampling calendar (Step 6) |
| `latitude`, `longitude` | float | ✅ obtainable (segment midpoint or GSI point) |
| `slope_deg` | float | ❌ **REQUIRED** — no computation exists today |
| `elevation_m` | float | ⚠️ obtainable but only as a coarse 7-point approximation; **REQUIRED: real per-segment DEM sample** before trusting this feature |
| `road_type` (highway_class) | categorical | ✅ obtainable now |
| `terrain_type` | categorical | ⚠️ obtainable, inherits elevation approximation's weakness |
| `historical_landslide_count` | int | ✅ obtainable now, **must be recomputed with a strict date cutoff per row** (Step 3) — current pipeline output is a single static count, not date-partitioned |
| `nearest_historical_landslide_distance_m` | float | ✅ obtainable now, same date-cutoff caveat |
| `rainfall_1d_mm` | float | ❌ **REQUIRED — not yet acquired** |
| `rainfall_3d_mm` | float | ❌ **REQUIRED — not yet acquired** |
| `rainfall_7d_mm` | float | ❌ **REQUIRED — not yet acquired** |
| `target` | 0/1 or graded | ❌ **REQUIRED — cannot be defined until event dates + weather + a negative-sampling design (Step 6) all exist** |

---

## STEP 6 — Positive/negative example construction strategy

**POSITIVE examples:** one row per (segment, date) where a GSI record was
matched (≤500m, per the existing spatial join) to that segment, dated to
that record's event date — contingent on that date being parseable (Step 9
gap).

**NEGATIVE examples — recommended strategy:** *conditions-matched sampling,
not arbitrary random roads/dates.* Concretely: for each segment that has ever
had at least one nearby recorded landslide (so it is known to be
geologically susceptible in principle), sample dates from that same
segment's history where rainfall conditions were **comparably severe**
(e.g. within the same percentile band of 3-day/7-day rainfall as observed
positive events in that region) but **no disruption was recorded** in the
forward window. This is preferred over uniform random sampling because:

- Randomly sampling arbitrary roads/dates would mostly draw dry-season,
  low-rainfall negatives, teaching the model to separate "monsoon vs.
  not" rather than "disruption vs. no-disruption under similar stress."
- Restricting negatives to segments with *some* known susceptibility (rather
  than the full network, most of which has literally zero observation
  history either way) avoids conflating "never assessed" with "assessed and
  safe" — consistent with the Step 2 reporting-bias concern.

This strategy **requires the rainfall dataset (Step 4)** to define
"comparably severe" and **requires structured event dates** to define the
forward window and exclude reporting gaps — both currently missing, per
Step 9/10.

---

## STEP 7/9 — Missing data (why we stop here)

1. **Rainfall/weather time series** — does not exist anywhere in this
   repository (verified by full-repo search). Blocks the entire weather-
   conditional target, the rolling-rainfall features, and the negative-
   sampling strategy in Step 6.
2. **Structured landslide event dates** — GSI source data has dates only as
   inconsistent free text (full datetime / year-only / `"NA"`), with no
   existing parser. Blocks event-level labeling and the leakage-safe
   temporal cutoff in Step 3.
3. **Real per-segment slope** — `slope_deg` is `None` for 100% of segments;
   no DEM or slope-derivation code exists in the repo. This is a REQUIRED
   feature per Step 3/5 and is currently entirely absent.
4. **Real per-segment elevation** — current values are a 7-point
   nearest-neighbor step function, not a measurement. Usable as a rough
   terrain proxy but not defensible as a precise ML feature without a real
   DEM sample per segment.
5. **Reporting-bias correction / exposure normalization** — even once dates
   and weather exist, the GSI inventory's opportunistic (non-systematic)
   collection means `count = 0` cannot be assumed to mean "safe"; this
   needs either an explicit exposure/effort adjustment or restricting scope
   to segments/corridors known to be consistently monitored.
6. **Land cover / geology / soil data** — not required at minimum, but
   commonly used alongside slope+rainfall in landslide-susceptibility
   modeling and worth acquiring if available (e.g. Bhukosh geology layers
   referenced in `README.md`) to reduce omitted-variable bias.

---

## STEP 8 — Test suite

No routing, risk-scoring, vehicle-simulation, weather-simulation, or
frontend code was modified. Ran the existing suite from `backend/`:

```
80 passed, 12 warnings in 8.30s
```

(warnings are pre-existing `pyproj`/NumPy deprecation notices in
`test_landslide_mapper.py`, unrelated to this change — no code paths were
altered by this task, this document is additive only.)

---

## Final recommendation

**NOT READY — MISSING:**
1. Historical daily rainfall/weather time series covering the corridor
   (West Kameng/Tawang at minimum) at ≥0.25° / daily resolution, spanning at
   least the GSI inventory's event period.
2. Structured, parseable landslide event dates (current GSI dates are
   inconsistent free text, sometimes absent entirely).
3. A real per-segment slope layer (currently unimplemented — always `None`).
4. A real per-segment DEM-sampled elevation (currently a 7-point
   approximation).

Until these are acquired, no defensible supervised target can be
constructed, and no model should be trained.
