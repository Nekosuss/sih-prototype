"""
Part 14: turns the existing real GSI-to-road spatial join
(app/data/landslide_mapper.py) into a per-segment, per-year event index
with an explicit temporal-precision flag -- the structure
build_segment_year_dataset.py needs to assign an honest label_status and
to compute strictly-prior-only historical features (no leakage).

--- Why this doesn't just reuse historical_landslide_count ---

RoadSegment.historical_landslide_count (populated by
landslide_mapper.enrich_segments_with_landslide_features) is a SINGLE
static all-time count -- exactly what
app/data/ml_dataset_inspection_part14.md Section 7.2 flags as a leakage
risk if reused as-is for a specific year's row: it doesn't know which
records happened before vs. after the year being labeled. This module
instead keeps every matched record's own (segment_id, year_or_None,
day_precise_date_or_None) so build_segment_year_dataset.py can apply a
strict "year < this row's year" cutoff per row.

--- Year extraction (measured, not guessed) ---

GSI records have no structured date field (see landslide_mapper.py /
app/data/README.md). Two real fields do carry date information:
  1. `slide_id` -- e.g. "ARN/WK/83A12/2016/01" almost always embeds a
     4-digit year between slashes. Present for ~86% of MATCHED-to-segment
     records (measured in the Part 14 inspection).
  2. `description_after_coordinates` -- free text, occasionally a full
     "D Month YYYY" date (measured: 14 of 104 MATCHED records, all in
     2016), otherwise just a bare year, otherwise nothing parseable.

Neither is a database of record -- both are regex extractions from
free-text/identifier fields, kept exactly as permissive/exact as the
inspection step verified. A record where NEITHER field yields a year is
kept as "undated" -- it contributes to a segment's undated-match count
(surfaced in the audit) but is NEVER assigned to a specific year's label.
"""
import re
from dataclasses import dataclass
from typing import Optional

from app.data.landslide_mapper import run_pipeline

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_FULL_DATE_RE = re.compile(rf"(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})")
_SLIDE_ID_YEAR_RE = re.compile(r"/(\d{4})/")

# Free-text keywords that would indicate a GENUINE negative/monitoring
# observation (e.g. "surveyed, found stable") rather than a reported event.
# Checked directly against the real corridor CSV (Part 14 inspection,
# Section on "documented non-event") -- zero records matched any of these
# as of this writing. Kept here (not hardcoded to "always empty") so this
# stays correct if the GSI extract is ever refreshed with different text.
_NEGATIVE_OBSERVATION_KEYWORDS = (
    "stable", "dormant", "no fresh", "inactive", "repaired", "stabilized",
    "stabilised", "no movement", "checked", "surveyed", "monitored", "cleared",
)


@dataclass(frozen=True)
class LandslideEvent:
    segment_id: str
    year: Optional[int]  # None if no year could be resolved at all
    day_precise: bool  # True if a full "D Month YYYY" date was found
    distance_to_road_m: float
    is_negative_observation: bool  # see _NEGATIVE_OBSERVATION_KEYWORDS above


def _year_from_slide_id(slide_id: str) -> Optional[int]:
    m = _SLIDE_ID_YEAR_RE.search(str(slide_id))
    if not m:
        return None
    year = int(m.group(1))
    return year if 1900 <= year <= 2100 else None


def _day_precise_date(description: str) -> Optional[str]:
    m = _FULL_DATE_RE.search(str(description))
    return m.group(0) if m else None


def _year_from_day_precise(description: str) -> Optional[int]:
    m = _FULL_DATE_RE.search(str(description))
    return int(m.group(3)) if m else None


def _is_negative_observation(raw_record: str) -> bool:
    text = str(raw_record).lower()
    return any(kw in text for kw in _NEGATIVE_OBSERVATION_KEYWORDS)


def build_landslide_events() -> list[LandslideEvent]:
    """
    Every MATCHED (<=500m of a real road segment) GSI corridor record,
    reduced to exactly what build_segment_year_dataset.py needs: which
    segment, which year (if resolvable), whether that year came from a
    full day-precise date, and whether the free text reads as a genuine
    negative observation. Pure read of the existing real spatial join
    (landslide_mapper.run_pipeline()) -- no new spatial matching, no
    fabricated coordinates.
    """
    mapped_df, _features_df = run_pipeline()
    matched = mapped_df[mapped_df["match_status"] == "MATCHED"]

    events: list[LandslideEvent] = []
    for _, row in matched.iterrows():
        day_precise = _day_precise_date(row["description_after_coordinates"]) is not None
        year = (
            _year_from_day_precise(row["description_after_coordinates"])
            if day_precise
            else _year_from_slide_id(row["slide_id"])
        )
        events.append(
            LandslideEvent(
                segment_id=row["matched_segment_id"],
                year=year,
                day_precise=day_precise,
                distance_to_road_m=float(row["distance_to_road_m"]),
                is_negative_observation=_is_negative_observation(row["raw_record"]),
            )
        )
    return events
