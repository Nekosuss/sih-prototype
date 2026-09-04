"""
Part 10: real IMD rainfall endpoints.

GET /weather/rainfall            real rainfall + weather_factor at an arbitrary lat/lon, for a given (or default) date
GET /weather/segments/{id}       same, using a real road segment's geometry midpoint as the point, plus the resulting
                                  Part 5 explainable risk (weather component now sourced from real rainfall)
GET /weather/corridor            a small dashboard-ready summary: the 7 named corridor locations' rainfall/weather_factor,
                                  plus which real segments currently show a high rainfall-driven weather_factor

Every value here traces back to the real IMD gridded-rainfall extraction
(app/data/rainfall_loader.py, backend/scripts/fetch_rainfall_data.py) --
never fabricated, and a missing/out-of-coverage observation is always
reported as such (see RainfallStatus), never silently treated as "no rain".

This is an ADDITIONAL input path alongside the existing Part 8 simulated
hazard mechanism (POST /hazards/simulate) -- it does not replace or modify
any existing endpoint's behavior. /segments/{id}/risk-aware (Part 5/8)
still works exactly as before and is unaffected by anything in this file.
"""
import datetime

from fastapi import APIRouter, HTTPException, Query

from app.config import DEFAULT_RAINFALL_OBSERVATION_DATE, RAINFALL_FACTOR_AT_MODERATE
from app.core import risk_engine
from app.core.hazard_state import combine_active_hazards_into_segment_context
from app.core.weather_factor import weather_factor_for_point, weather_factor_for_segment
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.rainfall_loader import SOURCE_NAME, RainfallStatus, get_default_rainfall_loader
from app.store.state_store import state_store

router = APIRouter(prefix="/weather")

# How many real segments to surface in the corridor summary's "high
# rainfall" list -- a dashboard convenience cap, not a hidden filter on
# which segments have real data (see get_corridor_weather_summary below).
MAX_HIGH_RAINFALL_SEGMENTS = 20


def _parse_date_param(date: str | None) -> datetime.date:
    raw = date or DEFAULT_RAINFALL_OBSERVATION_DATE
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date {raw!r}; expected YYYY-MM-DD") from None


def _observation_payload(result) -> dict:
    obs = result.observation
    return {
        "observation_date": obs.date.isoformat(),
        "requested": {"lat": obs.requested_lat, "lon": obs.requested_lon},
        "grid_cell": {"lat": obs.grid_lat, "lon": obs.grid_lon} if obs.grid_lat is not None else None,
        "rainfall_mm": obs.rainfall_mm,
        "status": obs.status.value,
        "is_real_observation": obs.status == RainfallStatus.ok,
        "weather_factor": result.weather_factor,
        "source": obs.source,
        "grid_resolution_deg": obs.grid_resolution_deg,
    }


@router.get("/rainfall")
def get_rainfall(
    lat: float = Query(..., description="Latitude of the point to query"),
    lon: float = Query(..., description="Longitude of the point to query"),
    date: str | None = Query(None, description="ISO date YYYY-MM-DD; defaults to the demo observation date"),
):
    obs_date = _parse_date_param(date)
    result = weather_factor_for_point(lat, lon, obs_date)
    return _observation_payload(result)


@router.get("/segments/{segment_id}")
def get_segment_weather(segment_id: str, date: str | None = Query(None)):
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")

    obs_date = _parse_date_param(date)
    result = weather_factor_for_segment(segment, obs_date)

    # incident_factor still comes from any active Part 8 simulated hazard on
    # this segment, exactly like /segments/{id}/risk-aware -- real rainfall
    # only ever supplies weather_factor, never incident_factor (see
    # core/weather_factor.py's module docstring).
    active_hazards = state_store.get_hazards(active_only=True)
    hazard_context = combine_active_hazards_into_segment_context(active_hazards).get(segment_id)
    incident_factor = hazard_context.incident_factor if hazard_context else None

    risk_result = risk_engine.assess_segment_risk(
        segment, weather_factor=result.weather_factor, incident_factor=incident_factor
    )

    payload = _observation_payload(result)
    representative_point = segment.geometry[len(segment.geometry) // 2]
    payload["segment_id"] = segment_id
    payload["representative_point"] = {"lat": representative_point.lat, "lng": representative_point.lng}
    payload["risk"] = risk_result
    return payload


@router.get("/corridor")
def get_corridor_weather_summary(date: str | None = Query(None)):
    obs_date = _parse_date_param(date)
    loader = get_default_rainfall_loader()

    locations = []
    for loc in DEMO_LOCATIONS:
        result = weather_factor_for_point(loc["lat"], loc["lng"], obs_date, loader=loader)
        obs = result.observation
        locations.append({
            "name": loc["name"],
            "lat": loc["lat"],
            "lon": loc["lng"],
            "rainfall_mm": obs.rainfall_mm,
            "status": obs.status.value,
            "weather_factor": result.weather_factor,
        })

    segments = state_store.get_segments()
    high_rainfall = []
    for segment in segments:
        result = weather_factor_for_segment(segment, obs_date, loader=loader)
        if result.weather_factor is not None and result.weather_factor >= RAINFALL_FACTOR_AT_MODERATE:
            high_rainfall.append({
                "segment_id": segment.id,
                "name": segment.name,
                "rainfall_mm": result.observation.rainfall_mm,
                "weather_factor": result.weather_factor,
            })
    high_rainfall.sort(key=lambda s: s["weather_factor"], reverse=True)

    return {
        "observation_date": obs_date.isoformat(),
        "source": SOURCE_NAME,
        "grid_resolution_deg": 0.25,
        "locations": locations,
        "high_rainfall_segment_count": len(high_rainfall),
        "high_rainfall_segments": high_rainfall[:MAX_HIGH_RAINFALL_SEGMENTS],
        "high_rainfall_threshold_weather_factor": RAINFALL_FACTOR_AT_MODERATE,
    }
