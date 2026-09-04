"""
Tests for the Part 5 explainable prototype risk engine
(core/risk_engine.assess_segment_risk and its component functions).

These use small synthetic RoadSegment objects (never the real network) so
each test isolates exactly one factor — see test_dem_integration.py /
test_landslide_mapper.py for tests against the real corridor data.
"""
import pytest

from app.config import (
    HISTORICAL_WEIGHT,
    INCIDENT_WEIGHT,
    RISK_LEVEL_THRESHOLDS,
    SLOPE_RISK_SATURATION_DEG,
    TERRAIN_WEIGHT,
    WEATHER_WEIGHT,
)
from app.core.risk_engine import assess_segment_risk, incident_factor_from_severity
from app.models.network import GeoPoint, RoadSegment, RoadType, TerrainType
from app.models.risk import RiskLevel


def make_segment(
    slope_deg=0.0,
    historical_landslide_count=0,
    nearest_landslide_distance_m=None,
    terrain_type=TerrainType.plain,
) -> RoadSegment:
    return RoadSegment(
        id="seg_test",
        from_node_id="a",
        to_node_id="b",
        road_type=RoadType.tertiary,
        distance_km=1.0,
        estimated_travel_time_min=2.0,
        geometry=[GeoPoint(lat=27.0, lng=92.0), GeoPoint(lat=27.01, lng=92.0)],
        terrain_type=terrain_type,
        slope_deg=slope_deg,
        elevation_m=500.0,
        landslide_susceptibility=0.0,
        flood_susceptibility=0.0,
        base_risk=0.05,
        current_risk_score=0.05,
        historical_landslide_count=historical_landslide_count,
        nearest_landslide_distance_m=nearest_landslide_distance_m,
    )


# 1. Low-risk flat segment, no historical observations -> low score
def test_flat_segment_no_history_is_low_risk():
    segment = make_segment(slope_deg=0.5, historical_landslide_count=0)
    result = assess_segment_risk(segment)
    assert result.risk_score < RISK_LEVEL_THRESHOLDS["moderate"]
    assert result.risk_level == RiskLevel.low
    assert result.breakdown.slope_risk == 0.0
    assert result.breakdown.historical_landslide_risk == 0.0


# 2. High-slope segment -> slope contribution increases
def test_high_slope_increases_slope_contribution():
    flat = assess_segment_risk(make_segment(slope_deg=0.5))
    steep = assess_segment_risk(make_segment(slope_deg=SLOPE_RISK_SATURATION_DEG))
    assert steep.breakdown.slope_risk > flat.breakdown.slope_risk
    assert steep.breakdown.slope_risk == 1.0
    assert steep.risk_score > flat.risk_score


# 3. Multiple historical observations -> historical contribution increases
def test_more_historical_observations_increase_contribution():
    one = assess_segment_risk(make_segment(historical_landslide_count=1, nearest_landslide_distance_m=250.0))
    many = assess_segment_risk(make_segment(historical_landslide_count=8, nearest_landslide_distance_m=250.0))
    assert many.breakdown.historical_landslide_risk > one.breakdown.historical_landslide_risk


def test_historical_contribution_is_bounded_and_sublinear_in_count():
    """A single heavily-observed segment must not dominate proportionally
    to its raw count — see app/config.py HISTORICAL_COUNT_REFERENCE."""
    count_2 = assess_segment_risk(make_segment(historical_landslide_count=2, nearest_landslide_distance_m=100.0))
    count_20 = assess_segment_risk(make_segment(historical_landslide_count=20, nearest_landslide_distance_m=100.0))
    # 10x the count must NOT translate into anywhere near 10x the risk contribution.
    assert count_20.breakdown.historical_landslide_risk <= count_2.breakdown.historical_landslide_risk * 2
    assert count_20.breakdown.historical_landslide_risk <= 1.0


def test_zero_historical_count_does_not_claim_safety():
    result = assess_segment_risk(make_segment(historical_landslide_count=0))
    assert result.breakdown.historical_landslide_risk == 0.0
    assert any("not a confirmed absence of hazard" in r or "coverage, not a" in r for r in result.reasons)


# 4. Nearby historical landslide -> contribution increases appropriately
def test_closer_historical_landslide_increases_contribution():
    far = assess_segment_risk(make_segment(historical_landslide_count=1, nearest_landslide_distance_m=480.0))
    near = assess_segment_risk(make_segment(historical_landslide_count=1, nearest_landslide_distance_m=5.0))
    assert near.breakdown.historical_landslide_risk > far.breakdown.historical_landslide_risk


# 5. High weather factor -> weather contribution increases
def test_high_weather_factor_increases_weather_contribution():
    segment = make_segment()
    clear = assess_segment_risk(segment, weather_factor=0.0)
    stormy = assess_segment_risk(segment, weather_factor=0.9)
    assert stormy.breakdown.weather_risk > clear.breakdown.weather_risk
    assert stormy.risk_score > clear.risk_score


def test_weather_factor_is_clamped_to_valid_range():
    segment = make_segment()
    result = assess_segment_risk(segment, weather_factor=5.0)  # out-of-range input
    assert result.breakdown.weather_risk == 1.0


def test_missing_weather_factor_defaults_to_zero_not_fabricated():
    result = assess_segment_risk(make_segment(), weather_factor=None)
    assert result.breakdown.weather_risk == 0.0
    assert any("No current weather context supplied" in r for r in result.reasons)


# 6. Active incident -> incident contribution increases
def test_active_incident_increases_incident_contribution():
    segment = make_segment()
    none = assess_segment_risk(segment, incident_factor=None)
    active = assess_segment_risk(segment, incident_factor=incident_factor_from_severity("major"))
    assert active.breakdown.incident_risk > none.breakdown.incident_risk
    assert active.risk_score > none.risk_score


def test_incident_factor_from_severity_known_and_unknown():
    assert incident_factor_from_severity("blocking") == 1.0
    with pytest.raises(ValueError):
        incident_factor_from_severity("catastrophic")


# 7. Combined high-risk factors -> high/critical prototype score
def test_combined_high_risk_factors_reach_high_or_critical():
    segment = make_segment(
        slope_deg=SLOPE_RISK_SATURATION_DEG,
        historical_landslide_count=10,
        nearest_landslide_distance_m=20.0,
    )
    result = assess_segment_risk(segment, weather_factor=0.9, incident_factor=0.8)
    assert result.risk_level in (RiskLevel.high, RiskLevel.critical)
    assert result.risk_score >= RISK_LEVEL_THRESHOLDS["high"]


# 8. Score always remains [0,1]
@pytest.mark.parametrize("slope_deg", [0.0, 2.0, 25.0, 89.9])
@pytest.mark.parametrize("count", [0, 1, 11, 500])
@pytest.mark.parametrize("weather_factor", [None, 0.0, 1.0, 50.0])
@pytest.mark.parametrize("incident_factor", [None, 0.0, 1.0, -3.0])
def test_score_always_within_unit_interval(slope_deg, count, weather_factor, incident_factor):
    segment = make_segment(slope_deg=slope_deg, historical_landslide_count=count, nearest_landslide_distance_m=1.0)
    result = assess_segment_risk(segment, weather_factor=weather_factor, incident_factor=incident_factor)
    assert 0.0 <= result.risk_score <= 1.0
    for value in (
        result.breakdown.slope_risk,
        result.breakdown.historical_landslide_risk,
        result.breakdown.weather_risk,
        result.breakdown.incident_risk,
    ):
        assert 0.0 <= value <= 1.0


# 9. Missing optional contextual inputs do not crash the engine
def test_missing_all_optional_inputs_does_not_crash():
    result = assess_segment_risk(make_segment())
    assert result.risk_score is not None


def test_missing_slope_and_distance_does_not_crash():
    segment = make_segment(slope_deg=None, historical_landslide_count=2, nearest_landslide_distance_m=None)
    result = assess_segment_risk(segment)
    assert result.breakdown.slope_risk == 0.0
    assert 0.0 <= result.breakdown.historical_landslide_risk <= 1.0
    assert any("Slope data unavailable" in r for r in result.reasons)


# 10. Risk breakdown components sum correctly according to configured weights
def test_risk_score_equals_weighted_sum_of_breakdown():
    segment = make_segment(slope_deg=10.0, historical_landslide_count=3, nearest_landslide_distance_m=200.0)
    result = assess_segment_risk(segment, weather_factor=0.4, incident_factor=0.3)
    expected = (
        TERRAIN_WEIGHT * result.breakdown.slope_risk
        + HISTORICAL_WEIGHT * result.breakdown.historical_landslide_risk
        + WEATHER_WEIGHT * result.breakdown.weather_risk
        + INCIDENT_WEIGHT * result.breakdown.incident_risk
    )
    assert result.risk_score == pytest.approx(expected, abs=1e-3)


def test_weights_sum_to_one():
    assert TERRAIN_WEIGHT + HISTORICAL_WEIGHT + WEATHER_WEIGHT + INCIDENT_WEIGHT == pytest.approx(1.0)


# Explanations must never claim a calibrated probability
def test_reasons_never_claim_a_calibrated_probability():
    segment = make_segment(slope_deg=20.0, historical_landslide_count=5, nearest_landslide_distance_m=50.0)
    result = assess_segment_risk(segment, weather_factor=0.7, incident_factor=0.6)
    joined = " ".join(result.reasons).lower()
    # The disclaimer is allowed (and expected) to use the word "probability"
    # to explicitly deny being one — what must never appear is an actual
    # probability-style claim, e.g. "73% probability of landslide".
    assert "% probability" not in joined
    assert "chance of" not in joined
    assert "probability of landslide" not in joined
    assert "prototype risk score" in joined
    assert "not a calibrated probability" in result.methodology_note.lower()


def test_risk_level_thresholds_are_respected():
    # Construct scores that land exactly at each configured threshold via
    # slope alone (weather/incident/historical all zero) to check the
    # boundary logic directly against RISK_LEVEL_THRESHOLDS.
    for level_name, threshold in RISK_LEVEL_THRESHOLDS.items():
        if threshold == 0.0:
            continue
        # slope_risk contributes TERRAIN_WEIGHT * slope_risk to risk_score;
        # solve for the slope_deg that puts risk_score exactly at threshold.
        needed_slope_risk = threshold / TERRAIN_WEIGHT
        if needed_slope_risk > 1.0:
            continue  # not reachable via slope alone at this weight; skip
        slope_deg = 2.0 + needed_slope_risk * (SLOPE_RISK_SATURATION_DEG - 2.0)
        result = assess_segment_risk(make_segment(slope_deg=slope_deg))
        assert result.risk_level.value == level_name or result.risk_score >= threshold
