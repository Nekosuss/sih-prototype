# ML Dataset Inspection — Part 14 (pre-training audit)

Status: **inspection and design only.** No model has been trained, no label
has been fabricated, no production code (`core/`, `simulation/`, frontend)
was touched. This document supersedes parts of `training_dataset_schema.md`
(Part 4.7) where the underlying data has since changed (real DEM slope is
now wired; 11 years of real rainfall now exist) — that document is kept
as-is for history; this one reflects the current, post-Part-13 state plus
the newly added top-level `data/` directory (11 years of IMD rainfall
NetCDF, 2015–2025).

All numbers below were measured directly against the real files in this
repository (`backend/app/data/network_loader.py::load_network()`,
`backend/app/data/landslide_mapper.py::run_pipeline()`, and
`scipy.io.netcdf_file` reads of `data/RF25_ind{year}_rfp25.nc`) — nothing
here is estimated from documentation alone.

---

## 1. Road network

- **2,964 real road segments**, 2,452 nodes (`load_network()`, unchanged
  from Parts 5–13).
- **100% of segments now have a real DEM-derived `slope_deg` and
  `elevation_m`** (Part 4.8). This resolves two of the four blockers Part
  4.7 flagged as missing — `slope_deg` was previously `None` for every
  segment; it is now populated for all 2,964.
  - `slope_deg`: min 0.00°, max 29.87°, mean 2.14° (degrees, magnitude —
    see `dem_processor.py`).
- Segment geometry bounding box: lat **[26.012, 27.750]**, lon **[91.542,
  92.976]** — entirely inside the rainfall grid subset described below.
- `landslide_hazard_score`/`flood_hazard_score` (APSAC hazard-zonation
  layer, Part 11): **0/2,964 populated** — unchanged, official data still
  not locally obtainable.

## 2. GSI landslide observations

- `data/gsi_landslides_arunachal.csv`: **1,219** raw records (whole
  Arunachal Pradesh).
- `data/gsi_landslides_corridor.csv`: **256** records — confirmed a proper
  subset of the file above (`slide_no` containment check), pre-filtered to
  this corridor's geographic area. The two top-level copies are
  byte-identical to the ones already used by the app under
  `backend/app/data/`; the "newly added" part of `data/` is the rainfall
  archive, not new landslide data.
- Spatially joining the 256 corridor records against the 2,964 real
  segments (existing `landslide_mapper.py`, 500m threshold):
  - **104 MATCHED** (within 500m of a real segment), **152 UNMATCHED**
    (too far from any currently-loaded road).
  - MATCHED records touch **33 distinct segments** (1.11% of 2,964).
    `sum(historical_landslide_count)` across all segments = **104** (no
    deduplication — see Leakage §7 below).

### Date quality (measured, not assumed)

GSI dates are **not a structured field** — only free text
(`description_after_coordinates`) plus a year embedded in most
`slide_id` strings (e.g. `ARN/WK/83A12/2016/01`).

| Precision | Corridor (256) | MATCHED-to-segment (104) |
|---|---|---|
| Year resolvable from `slide_id` | 199 (77.7%) | 90 (86.5%) |
| Full day-precision date in free text | 14 (5.5%) | **14 (13.5%)** |
| No date recoverable at all | 57 (22.3%) | 14 (13.5%) |

**All 14 day-precision MATCHED records fall on only 3 calendar dates**: 22
Apr 2016, 23 Apr 2016 (one apparent multi-segment storm event, 13 records
across 7 segments), and 1 Jul 2016 (1 record, 1 segment). This is the
entire population of genuinely day-precise, road-matched landslide events
in the corridor today.

Year distribution of MATCHED records: `{2008: 5, 2014: 2, 2016: 28, 2021:
55}` — note **no 2022 or 2025 records matched a real segment** (the
corridor CSV has 73 + 28 raw records in those years, but none within 500m
of the currently loaded road network).

## 3. Rainfall grid (new data)

`data/RF25_ind{2015..2025}_rfp25.nc` — 11 files, one per year, IMD 0.25°×0.25°
daily gridded rainfall (Pai et al. 2014). Verified schema (identical every
year): variables `LONGITUDE`(135), `LATITUDE`(129), `TIME`, `RAINFALL`;
full-India grid lon 66.5–100.0°E, lat 6.5–38.5°N; missing sentinel
`-999.0`.

- **Date coverage: 2015-01-01 → 2025-12-31 inclusive**, zero gaps (every
  year's `TIME` axis is fully consecutive daily; leap years correctly have
  366 steps).
- **Corridor-bbox subset (lat 25.75–28.00, lon 91.50–93.00, matching the
  existing `fetch_rainfall_data.py` bbox): 10×7 = 70 grid cells per day**,
  identical count every year.
- **Missing values: exactly 4.29% every year** (1,095–1,098 of 25,550–25,620
  cell-days) — traced to **3 specific grid cells that are permanently
  `-999` on every single day of every year checked**: (27.25°N, 91.50°E),
  (27.25°N, 91.75°E), (27.50°N, 91.50°E) — the northwest corner of the
  corridor bbox (near the Bhutan border, west of Bomdila/Dirang), almost
  certainly ungauged terrain in IMD's interpolation. Every other cell is
  fully populated with real (including real `0.0` "no rain") values.
- **No real road segment's nearest grid cell falls in one of those 3
  permanently-missing cells** (checked against all 2,964 segment
  midpoints) — the gap doesn't blind any part of the actual road network.
- Spot-check: rainfall on 20–23 Apr 2016 near the two day-precise event
  clusters shows real, non-missing, elevated values (10–32 mm/day),
  consistent with a genuine rainfall-triggering signal rather than a
  data artifact.

## 4. Spatial overlap

Road network bbox is fully contained within the rainfall corridor subset's
bbox with margin on all sides — every segment can be assigned a nearest
rainfall grid cell. Caveat: **0.25° (~25–28km) grid cells are much coarser
than the spacing between distinct road segments** — many segments,
especially in dense sections, will share an identical nearest grid cell
and therefore identical rainfall features on any given day (see Leakage
§7).

## 5. Temporal overlap between rainfall and landslide observations

Rainfall coverage is 2015–2025. Of the MATCHED, dated corridor records:

- **2008 (5 records) and 2014 (2 records) predate rainfall coverage
  entirely — permanently unusable for any rainfall-conditional label.**
- **2016 (28 records → 9 distinct segment-year pairs) and 2021 (55 records
  → 21 distinct segment-year pairs) overlap rainfall coverage** — 83
  matched+dated records, **30 distinct (segment, year) positive pairs**,
  touching **29 distinct segments**.
- Of those 30 segment-year positives, only the 2016 ones (9 of 30) have
  day-precision (all on 3 calendar dates, per §2). **None of the 21 2021
  segment-year positives have a resolvable day** — they can only be placed
  at year granularity.

## 6. Estimated sample counts (multiple honest framings — pick none blindly)

| Framing | Segment-\* rows | Positives | Positive rate |
|---|---|---|---|
| Naive full cross-product: all 2,964 segments × all rainfall-covered days (~4,018 days) | ~11.9M | 3 real event-days (≤8 segments) | ~0.00007% — **meaningless; see §8** |
| Restricted to the 33 ever-observed segments × 11 years | 363 segment-years | 30 | 8.3% |
| Restricted to the 33 ever-observed segments × ~4,018 rainfall-covered days | ~132,594 segment-days | **3 real calendar dates** (rest would be *assumed* negatives, not confirmed — see §8) | not usable as-is |
| Day-precision-only (the only rows with a real, defensible single-day label) | 3 event-days × 7–8 segments ≈ **21–24 rows** | all positive by construction | N/A — far too small to train/test split at all |

**No framing above produces a dataset that is both large enough and
honestly labeled at day granularity.** The only real day-precision signal
in the entire corridor is 3 calendar dates. A segment-YEAR framing (363
candidate rows, 30 positive, restricted to segments with any known
history) is the coarsest granularity that is both non-fabricated and has
enough positive examples to be worth discussing further — see §9.

## 7. Potential data leakage problems (must be designed around, not just noted)

1. **Future-rainfall leakage**: any feature window (1d/3d/7d trailing
   rainfall) must be computed using only rainfall dated ≤ the row's own
   observation date. Never include the triggering day's rainfall in a
   "predict before it happens" framing without deciding explicitly whether
   same-day rainfall counts as a feature (nowcasting) or must be excluded
   (forecasting) — these are different tasks.
2. **Static-count leakage**: `historical_landslide_count` /
   `nearest_landslide_distance_m` are currently computed **once, over all
   time**, no date cutoff. Using them as a feature for a 2016 training row
   as-is would leak the 2021 event forward in time (and vice versa). Any
   per-row count must be recomputed using **only records strictly before
   that row's date** — trivial for the 14 day-precision records, but
   **ambiguous for year-only records**: a record dated only "2021" cannot
   be safely excluded/included relative to a row also dated "2021" without
   guessing a day. Safest rule: drop same-year self-records from that
   row's own feature computation, and never train/test-split within the
   same calendar year for a segment that has an undated-day event that
   year.
3. **Segment-identity leakage via random splitting**: because only 33
   segments have any history, a random row-level train/test split will
   likely place the *same segment's* 2016 row in train and its 2021 row in
   test (`seg_1217478195_0` has positives in both years). The model could
   then learn "this segment_id/location is risky" rather than a
   generalizable rainfall-response pattern. **Must group-split by
   segment** (or by geographic sub-corridor), never by row.
4. **Candidate-pool selection leakage**: the "restrict negatives to
   ever-observed segments" strategy (Part 4.7 §6) itself uses each
   segment's *entire lifetime* record to decide whether it's eligible as a
   candidate at all. A 2016 row's candidacy must not be decided using the
   fact that the *same* segment later had a 2021 report — that is
   information from the future. Candidate-pool membership must be
   re-evaluated per row using only history available up to that row's own
   date.
5. **Duplicate reports, not independent events**: `seg_560851692_0` alone
   has 10 separate MATCHED records in 2016 (likely repeat/multiple
   observations of the same slide or nearby debris along one stretch).
   These must be deduplicated to **one label per (segment, calendar day or
   year)** before counting positives — otherwise sample counts and
   apparent frequency are inflated by reporting multiplicity, not distinct
   events.
6. **Grid-resolution aliasing**: per §4, many segments share one 0.25°
   rainfall cell. A model could appear to discriminate by rainfall while
   actually only discriminating by which of ~70 grid cells a segment
   happens to fall in — indistinguishable from genuine segment-level
   signal without also including segment-level static features (slope,
   history) as the actual differentiator.

## 8. Limitations of the available labels (why "no report" ≠ "safe")

- The GSI inventory is an **opportunistic field-observation record**, not
  a systematic periodic survey of every segment. A segment with zero
  matched records may mean "never had a landslide" or may simply mean "no
  one recorded one there" (per Part 4.7 §2 — still true, unchanged by the
  new rainfall data).
- **Reporting is spatially clustered near named locations** (Bhalukpong
  Circuit House, Tawang district records dominate the day-precise set) —
  segments far from named towns are structurally less likely to have *any*
  record regardless of true hazard, which the model cannot distinguish
  from "genuinely lower risk" using this data alone.
- 2,931 of 2,964 segments (98.9%) have **zero recorded history at all** —
  not "confirmed safe," simply "unobserved." These segments must not be
  used as labeled negatives under any framing; at most they can be
  reported as "no historical signal available" (mirrors how the existing
  rule-based risk engine already treats `historical_landslide_count == 0`
  — see `risk_engine.py`'s own reasons text).
- Even the 30 usable segment-year positives are not evenly distributed:
  21 of 30 come from a single year (2021), so a model trained on this data
  would be disproportionately learning "what 2021 looked like" rather than
  a rainfall-response pattern generalizable across seasons/years.

## 9. Conclusion of this inspection

**A day-granularity "segment-day" model is not defensible today** — only 3
real calendar dates have day-precision, road-matched labels, touching at
most 8 segments. Any larger "segment-day" table would necessarily assume
negatives are "confirmed safe" for the other ~4,015 days per segment,
which directly contradicts the constraint not to treat absence-of-report
as safety.

**A coarser segment-YEAR framing is the most defensible starting point**:
363 candidate rows (33 ever-observed segments × 11 rainfall-covered
years), 30 positive, real (not fabricated) labels, real terrain features
(100% DEM slope/elevation coverage), and real annual rainfall aggregates —
still with the leakage safeguards in §7 (group-split by segment, per-row
date-cutoff on historical counts, no same-year self-leakage) and the
labeling caveats in §8 made explicit in any model card / UI copy (never
call this a "probability," consistent with the existing risk engine's own
`methodology_note` language).

Backend test suite unaffected by this inspection (no code changed):
596 passed (unchanged from Part 13).
