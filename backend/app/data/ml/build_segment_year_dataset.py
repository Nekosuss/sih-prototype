"""
Part 14: builds the full segment-year feature table -- every real road
segment currently loaded, crossed with every year the real rainfall
archive covers (2,964 segments x 11 years = 32,604 rows) -- with an
honest label_status per row and strict temporal cutoffs on every
history-derived feature. See app/data/ml_dataset_inspection_part14.md for
the inspection this design is based on.

--- What this deliberately does NOT do ---

- Does NOT force every undocumented segment-year to label=0. A row with no
  qualifying evidence either way is label_status="unobserved", label=NaN
  -- see the module docstring of ml/landslide_year_index.py for why
  "no report" is never treated as "confirmed safe" in this dataset.
- Does NOT train, fit, or evaluate any model. This script only writes a
  CSV and an audit report.
- Does NOT modify app/core/risk_engine.py, app/core/routing_engine.py, or
  any other production module -- this package is never imported by them.

--- label_status (exactly one of three, per row) ---

  "event"                -- >=1 MATCHED GSI record's resolved year equals
                             this row's year (see landslide_year_index.py).
                             label = 1.0
  "non_event_documented"  -- a genuine negative/monitoring observation
                             exists for this segment in this exact year
                             (checked via _NEGATIVE_OBSERVATION_KEYWORDS in
                             landslide_year_index.py). label = 0.0.
                             Measured to be EMPTY in the current GSI
                             extract -- see the audit report; the code path
                             exists so this stays correct if the source
                             data is ever refreshed, not because any row
                             uses it today.
  "unobserved"            -- neither of the above. label = NaN. This is
                             the label_status for 2,931 of 2,964 segments'
                             every single year (no historical signal at
                             all, not "confirmed safe").

--- Strict temporal cutoffs (leakage rule, Part 14 inspection Section 7) ---

`historical_landslide_count_prior` / `nearest_historical_landslide_distance_m_prior`
for a (segment, year) row are computed using ONLY that segment's matched
records whose resolved year is STRICTLY LESS THAN the row's year. A
same-year record (whether or not it's the one labeling this row "event")
is never counted in that row's own prior-history features -- this applies
uniformly, including to segments with multiple same-year reports.
Records with no resolvable year at all are excluded from every row's
prior-history count (cannot be placed in time) -- see
`undated_historical_match_count` (segment-level, constant across a
segment's 11 rows) for that real, separately-surfaced limitation.

Rainfall features are computed for the row's OWN year (contemporaneous
with the label, by construction of a segment-YEAR table) -- Section 5/9 of
the inspection doc already flags that year-only-dated events cannot be
split into "before" vs. "after" a specific day within that year, so this
is a documented, acknowledged simplification, not an oversight.
"""
import csv
from collections import defaultdict
from pathlib import Path

from app.data.ml.landslide_year_index import build_landslide_events
from app.data.ml.rainfall_archive_loader import available_years, load_year_corridor_cells, nearest_cell
from app.data.network_loader import load_network

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "derived"
DATASET_CSV = OUTPUT_DIR / "segment_year_dataset.csv"
AUDIT_MD = OUTPUT_DIR / "segment_year_dataset_audit.md"

COLUMNS = [
    "segment_id", "year",
    "road_type", "terrain_type", "distance_km", "slope_deg", "elevation_m",
    "landslide_hazard_score",
    "historical_landslide_count_prior", "nearest_historical_landslide_distance_m_prior",
    "undated_historical_match_count",
    "rainfall_grid_lat", "rainfall_grid_lon",
    "annual_rainfall_mm", "monsoon_jun_sep_rainfall_mm", "max_daily_rainfall_mm",
    "rainy_days_count", "rainfall_missing_days",
    "event_report_count", "event_day_precision",
    "label_status", "label",
]


def build_dataset() -> list[dict]:
    nodes, segments = load_network()
    events = build_landslide_events()

    # Index events per segment for O(1) lookup while building rows.
    events_by_segment: dict[str, list] = defaultdict(list)
    for e in events:
        events_by_segment[e.segment_id].append(e)

    years = available_years()
    if not years:
        raise RuntimeError("No rainfall archive files found -- see rainfall_archive_loader.py's docstring.")

    # Rainfall cells are read once per year (not once per segment) -- 11
    # NetCDF reads total, not 2,964 x 11.
    rainfall_cells_by_year = {year: load_year_corridor_cells(year) for year in years}

    rows: list[dict] = []
    for segment in segments:
        seg_events = events_by_segment.get(segment.id, [])
        undated_count = sum(1 for e in seg_events if e.year is None)

        mid = segment.geometry[len(segment.geometry) // 2]

        for year in years:
            prior_events = [e for e in seg_events if e.year is not None and e.year < year]
            this_year_events = [e for e in seg_events if e.year == year]

            historical_count_prior = len(prior_events)
            nearest_distance_prior = (
                round(min(e.distance_to_road_m for e in prior_events), 2) if prior_events else None
            )

            has_positive_this_year = any(not e.is_negative_observation for e in this_year_events)
            has_only_negative_this_year = bool(this_year_events) and not has_positive_this_year

            if has_positive_this_year:
                label_status, label = "event", 1.0
            elif has_only_negative_this_year:
                label_status, label = "non_event_documented", 0.0
            else:
                label_status, label = "unobserved", None

            cells = rainfall_cells_by_year[year]
            cell_key = nearest_cell(mid.lat, mid.lng, cells)
            cell = cells[cell_key]

            rows.append({
                "segment_id": segment.id,
                "year": year,
                "road_type": segment.road_type.value,
                "terrain_type": segment.terrain_type.value,
                "distance_km": segment.distance_km,
                "slope_deg": segment.slope_deg,
                "elevation_m": segment.elevation_m,
                "landslide_hazard_score": segment.landslide_hazard_score,
                "historical_landslide_count_prior": historical_count_prior,
                "nearest_historical_landslide_distance_m_prior": nearest_distance_prior,
                "undated_historical_match_count": undated_count,
                "rainfall_grid_lat": cell.grid_lat,
                "rainfall_grid_lon": cell.grid_lon,
                "annual_rainfall_mm": cell.annual_rainfall_mm,
                "monsoon_jun_sep_rainfall_mm": cell.monsoon_jun_sep_rainfall_mm,
                "max_daily_rainfall_mm": cell.max_daily_rainfall_mm,
                "rainy_days_count": cell.rainy_days_count,
                "rainfall_missing_days": cell.missing_days,
                "event_report_count": len(this_year_events),
                "event_day_precision": any(e.day_precise for e in this_year_events),
                "label_status": label_status,
                "label": label,
            })
    return rows


def write_csv(rows: list[dict], path: Path = DATASET_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_audit(rows: list[dict]) -> str:
    n_total = len(rows)
    n_segments = len({r["segment_id"] for r in rows})
    n_years = len({r["year"] for r in rows})

    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["label_status"]] += 1

    by_status_year: dict[tuple, int] = defaultdict(int)
    for r in rows:
        by_status_year[(r["year"], r["label_status"])] += 1

    event_rows = [r for r in rows if r["label_status"] == "event"]
    day_precise_events = sum(1 for r in event_rows if r["event_day_precision"])
    distinct_event_segments = len({r["segment_id"] for r in event_rows})

    undated_segments = len({r["segment_id"] for r in rows if r["undated_historical_match_count"] > 0})

    rainfall_missing_rows = sum(1 for r in rows if r["rainfall_missing_days"] and r["rainfall_missing_days"] > 0)

    years_sorted = sorted({r["year"] for r in rows})

    lines = []
    lines.append("# Segment-Year Dataset — Coverage Audit (Part 14)\n")
    lines.append(
        "Generated by `app/data/ml/build_segment_year_dataset.py`. No model has been "
        "trained; this only reports what the constructed table actually contains.\n"
    )
    lines.append("## Overall shape\n")
    lines.append(f"- Total rows: **{n_total}**")
    lines.append(f"- Distinct segments: **{n_segments}**")
    lines.append(f"- Distinct years: **{n_years}** ({years_sorted[0]}-{years_sorted[-1]})")
    lines.append(f"- Expected shape: {n_segments} segments x {n_years} years = {n_segments * n_years}")
    lines.append(f"- Actual row count matches expected shape: **{n_total == n_segments * n_years}**\n")

    lines.append("## Rows per label_status\n")
    lines.append("| label_status | rows | % of total |")
    lines.append("|---|---|---|")
    for status in ["event", "non_event_documented", "unobserved"]:
        count = by_status.get(status, 0)
        lines.append(f"| {status} | {count} | {100*count/n_total:.3f}% |")
    lines.append("")

    lines.append(
        "`non_event_documented` is 0 by measurement, not by omission — the code path "
        "that assigns it exists (see landslide_year_index.py's "
        "_NEGATIVE_OBSERVATION_KEYWORDS) but no GSI record in the current corridor "
        "extract contains language describing a genuine negative/monitoring "
        "observation. Every non-\"event\" row is honestly `unobserved`, not assumed safe.\n"
    )

    lines.append("## label_status by year\n")
    lines.append("| year | event | non_event_documented | unobserved |")
    lines.append("|---|---|---|---|")
    for year in years_sorted:
        e = by_status_year.get((year, "event"), 0)
        ne = by_status_year.get((year, "non_event_documented"), 0)
        u = by_status_year.get((year, "unobserved"), 0)
        lines.append(f"| {year} | {e} | {ne} | {u} |")
    lines.append("")

    lines.append('## Positive ("event") rows detail\n')
    lines.append(f"- Total `event` rows: **{len(event_rows)}**")
    lines.append(f"- Distinct segments with >=1 `event` row: **{distinct_event_segments}**")
    lines.append(
        f"- Of those, rows where the underlying record(s) include a full "
        f"day-precision date: **{day_precise_events}** (all fall in 2016 per the "
        "inspection doc; every 2021 event row is year-precision only)\n"
    )

    lines.append("## Historical-feature coverage (prior-cutoff)\n")
    n_with_prior_history = sum(1 for r in rows if r["historical_landslide_count_prior"] > 0)
    lines.append(
        f"- Rows with `historical_landslide_count_prior` > 0 (i.e. this segment had "
        f"at least one dated match strictly before this row's year): **{n_with_prior_history}** "
        f"({100*n_with_prior_history/n_total:.2f}%)"
    )
    lines.append(
        f"- Distinct segments carrying an `undated_historical_match_count` > 0 "
        f"(real matched landslide evidence that could not be placed in any specific "
        f"year, and therefore never contributes to any row's label or "
        f"prior-history count): **{undated_segments}**\n"
    )

    lines.append("## Rainfall feature coverage\n")
    lines.append(
        f"- Rows where the nearest rainfall grid cell had >=1 missing day that year: "
        f"**{rainfall_missing_rows}** / {n_total}"
    )
    lines.append(
        "- (Expected near-zero: the inspection confirmed no real segment's nearest "
        "cell is one of the 3 permanently-ungauged cells; a handful of legitimate "
        "isolated missing days elsewhere would still show up here honestly rather "
        "than being silently treated as 0mm.)\n"
    )

    lines.append("## Reminders for anyone using this table\n")
    lines.append("- `label` is NaN (not 0) for every `unobserved` row — filter or handle explicitly, never `.fillna(0)`.")
    lines.append("- Do not train/test split randomly by row: split by `segment_id` (group split) — see the inspection doc's Section 7.3.")
    lines.append("- Rainfall features are contemporaneous with the row's year, not pre-event-only, for events without a day-precise date (Section 9 caveat).")
    lines.append("- This is a design/build artifact only — no model has been trained on this table.")

    return "\n".join(lines)


def main():
    print("Building segment-year dataset...")
    rows = build_dataset()
    write_csv(rows)
    print(f"Wrote {len(rows)} rows to {DATASET_CSV}")

    audit_text = build_audit(rows)
    AUDIT_MD.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_MD.write_text(audit_text, encoding="utf-8")
    print(f"Wrote audit report to {AUDIT_MD}")
    print()
    print(audit_text)


if __name__ == "__main__":
    main()
