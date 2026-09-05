# Model Card — Segment-Year Landslide Ranking Prototype, v2 (17-feature, no rainfall)

**Experiment ID:** `part15a_segment_year_v2_17feature` (Part 15A)
**Predecessor:** `part14_segment_year_v1` (21-feature, `../` — unchanged, not overwritten)
**Status:** Prototype research artifact. **Not integrated into production.**

## Why this version exists

Part 15's production feature-parity audit found that 4 of v1's 21 features
(`annual_rainfall_mm`, `monsoon_jun_sep_rainfall_mm`, `max_daily_rainfall_mm`,
`rainy_days_count`) are full-calendar-year aggregates that cannot be
honestly computed at inference time in production — see
`ml_feature_parity_part15a.md` for the full investigation. This version
removes those 4 features and keeps everything else — same dataset, same
labels, same LOGO grouping, same model hyperparameters — identical to v1.

## Result (see validation_metadata.json for full numbers)

| model | within-terrain mean percentile (v1, 21-feature) | within-terrain mean percentile (v2, 17-feature) |
|---|---|---|
| Random Forest | 78.63 | 75.59 |
| Logistic Regression | 72.94 | 74.92 |

Both v2 models remain far above the rule-based production baseline
(58.2 mean within-terrain percentile, AUC ≈ 0.535 — essentially chance).

## Everything else

Identical caveats, limitations, and "not intended for" restrictions as
`../MODEL_CARD.md` (v1) — this document does not repeat them in full; see
that file. In particular: **not intended for autonomous safety decisions,
not a calibrated probability, not a replacement for domain experts, not
for production deployment without additional validation.**
