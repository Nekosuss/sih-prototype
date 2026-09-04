"""
Runs the Part 5 explainable prototype risk engine (core/risk_engine.py)
against representative REAL segments of the loaded corridor and prints
their score/breakdown/explanation. This is a READ-ONLY reporting script —
it does not change routing, does not write RoadSegment.current_risk_score,
and does not fabricate any input: every slope_deg/historical_landslide_count/
nearest_landslide_distance_m value below comes straight from the real
DEM-sampling and GSI-matching pipelines (Part 4.8 / the landslide_mapper
spatial join), not a hard-coded expectation.

--- Usage ---
    cd backend
    python -m app.data.risk_engine_validation
"""
from app.core.geo import haversine_km
from app.core.risk_engine import assess_segment_risk
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.network_loader import load_network


def _nearest_segment(segments, lat, lng):
    def mid(s):
        return s.geometry[len(s.geometry) // 2]

    return min(segments, key=lambda s: haversine_km(lat, lng, mid(s).lat, mid(s).lng))


def main():
    nodes, segments = load_network()

    print("=== Prototype risk score for the nearest real segment to each named location ===")
    print("(no weather/incident context supplied -- weather_risk and incident_risk are both 0)")
    print()
    for loc in DEMO_LOCATIONS:
        segment = _nearest_segment(segments, loc["lat"], loc["lng"])
        result = assess_segment_risk(segment)
        print(f"--- {loc['name']} -> {segment.id} ({segment.name or 'unnamed way'}) ---")
        print(f"  elevation_m={segment.elevation_m}, slope_deg={segment.slope_deg}, terrain_type={segment.terrain_type.value}")
        print(f"  historical_landslide_count={segment.historical_landslide_count}, "
              f"nearest_landslide_distance_m={segment.nearest_landslide_distance_m}")
        print(f"  risk_score={result.risk_score}  risk_level={result.risk_level.value}")
        print(f"  breakdown={result.breakdown.model_dump()}")
        for reason in result.reasons:
            print(f"    - {reason}")
        print()

    print("=== The 5 real segments with the highest prototype risk score in the whole corridor ===")
    print("(again: no weather/incident context -- this ranks on real slope + real historical evidence only)")
    scored = sorted(
        (assess_segment_risk(s) for s in segments),
        key=lambda r: r.risk_score,
        reverse=True,
    )[:5]
    segments_by_id = {s.id: s for s in segments}
    for result in scored:
        segment = segments_by_id[result.segment_id]
        print(f"  {result.segment_id} ({segment.name or 'unnamed way'}): "
              f"risk_score={result.risk_score} level={result.risk_level.value} "
              f"slope_deg={segment.slope_deg} historical_count={segment.historical_landslide_count}")

    print()
    print("=== Same top segment, illustrating weather/incident context sensitivity ===")
    if scored:
        top_segment = segments_by_id[scored[0].segment_id]
        baseline = assess_segment_risk(top_segment)
        with_weather = assess_segment_risk(top_segment, weather_factor=0.8)
        with_incident = assess_segment_risk(top_segment, weather_factor=0.8, incident_factor=0.8)
        print(f"  no context supplied:            risk_score={baseline.risk_score} ({baseline.risk_level.value})")
        print(f"  + weather_factor=0.8:           risk_score={with_weather.risk_score} ({with_weather.risk_level.value})")
        print(f"  + weather_factor=0.8, incident=0.8: risk_score={with_incident.risk_score} ({with_incident.risk_level.value})")


if __name__ == "__main__":
    main()
