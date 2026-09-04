"""
POST /hazards/simulate    trigger a new deterministic SIMULATED hazard on real segment(s)
GET  /hazards             list hazard events (active only by default)
POST /hazards/{id}/clear  deactivate one hazard (its dynamic effect stops applying)
POST /hazards/reset       remove ALL hazard history (deterministic demo reset)

Part 8. Every hazard here is a SIMULATED demo input — never a live weather
feed or field report; see app/models/hazard.py's module docstring and
HAZARD_TYPE_LABEL (every `type`/`message` value is prefixed "SIMULATED").

Thin by design, matching routes_routing.py/routes_network.py: this module
only validates input (real segment ids) and shapes the response;
core/hazard_state.py builds the actual HazardEvent, and StateStore is the
only place hazards are stored (in-memory, no database).

--- Part 11 additions (same router, a DIFFERENT concept) ---

GET /hazards/layers               provenance/availability of the official
                                   landslide/flood hazard-ZONATION layers
GET /hazards/segments/{segment_id} one segment's landslide/flood hazard
                                    zonation result (static, precomputed at
                                    network-load time -- see
                                    app/data/hazard_layer_mapper.py)

These are NOT simulated events -- they read the real (or, currently,
honestly-unavailable) spatial hazard-zonation layer described in
app/data/hazard_layer_loader.py's module docstring. Kept in this same
router because they're still conceptually "hazard" endpoints and adding a
whole new router file for two GET endpoints would be unnecessary surface
area; the docstrings/response shapes make the distinction from the
simulated-event endpoints above unambiguous.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.hazard_state import build_hazard_event
from app.data.hazard_layer_loader import get_default_hazard_layer_loader
from app.models.hazard import HazardEvent, HazardSeverity, HazardType
from app.store.state_store import state_store

router = APIRouter(prefix="/hazards")


class SimulateHazardRequest(BaseModel):
    type: HazardType
    severity: HazardSeverity
    affected_segment_ids: list[str] = Field(min_length=1)


@router.post("/simulate", response_model=HazardEvent)
def simulate_hazard(request: SimulateHazardRequest):
    unknown = [sid for sid in request.affected_segment_ids if state_store.get_segment(sid) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown segment id(s): {unknown}")

    event = build_hazard_event(request.type, request.severity, request.affected_segment_ids)
    state_store.add_hazard(event)
    return event


@router.get("", response_model=list[HazardEvent])
def list_hazards(active_only: bool = True):
    return state_store.get_hazards(active_only=active_only)


@router.post("/{hazard_id}/clear", response_model=HazardEvent)
def clear_hazard(hazard_id: str):
    if state_store.get_hazard(hazard_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown hazard: {hazard_id}")
    return state_store.clear_hazard(hazard_id)


@router.post("/reset")
def reset_hazards():
    state_store.reset_hazards()
    return {"status": "ok", "active_hazards": 0}


# ---------------------------------------------------------------------------
# Part 11: real landslide/flood hazard-zonation layers (NOT simulated).
# ---------------------------------------------------------------------------


def _layer_summary(layer, official_page: str) -> dict:
    return {
        "dataset": layer.source_name,
        "official_page": official_page,
        "loaded": layer.is_loaded,
        "feature_count": layer.feature_count,
        "access_status": (
            "loaded from a local file" if layer.is_loaded
            else "official layer not locally available -- APSAC/SRSAC requires a manual data "
                 "request rather than direct download (see app/data/hazard_layer_loader.py)"
        ),
    }


@router.get("/layers")
def get_hazard_layers():
    """Provenance/availability of the two Part 11 spatial hazard-zonation
    layers -- never a per-segment value (see /hazards/segments/{id} for
    that). Also reports how many currently-loaded real segments have real
    coverage from each layer, for an honest corridor-wide picture even
    though (as of this delivery) that count is 0 for both."""
    from app.config import HAZARD_CLASS_TO_SCORE

    loader = get_default_hazard_layer_loader()
    segments = state_store.get_segments()
    landslide_covered = sum(1 for s in segments if s.landslide_hazard_score is not None)
    flood_covered = sum(1 for s in segments if s.flood_hazard_score is not None)

    return {
        "landslide_hazard": _layer_summary(loader.landslide_layer, "https://www.srsac.arunachal.gov.in/admin/geospatial.html"),
        "flood_hazard": _layer_summary(loader.flood_layer, "https://www.srsac.arunachal.gov.in/admin/geospatial.html"),
        "class_to_normalized_score": HAZARD_CLASS_TO_SCORE,
        "corridor_coverage": {
            "total_segments": len(segments),
            "segments_with_real_landslide_hazard_data": landslide_covered,
            "segments_with_real_flood_hazard_data": flood_covered,
        },
    }


@router.get("/segments/{segment_id}")
def get_segment_hazard_layers(segment_id: str):
    """One segment's Part 11 landslide/flood hazard-zonation result --
    STATIC fields precomputed at network-load time (see
    app/data/hazard_layer_mapper.py), not a live per-request spatial query
    (contrast with GET /weather/segments/{id}, which IS live because
    rainfall varies by date). Reflects whatever is currently in
    RoadSegment.landslide_hazard_*/flood_hazard_* -- None/"no_coverage"
    for every segment until a real official layer is obtained and this
    mapper is re-run."""
    segment = state_store.get_segment(segment_id)
    if segment is None:
        raise HTTPException(status_code=404, detail=f"Unknown segment: {segment_id}")

    return {
        "segment_id": segment_id,
        "landslide_hazard": {
            "status": "ok" if segment.landslide_hazard_score is not None else "no_coverage",
            "hazard_class": segment.landslide_hazard_class,
            "hazard_score": segment.landslide_hazard_score,
            "source": segment.hazard_layer_source.get("landslide_hazard"),
        },
        "flood_hazard": {
            "status": "ok" if segment.flood_hazard_score is not None else "no_coverage",
            "hazard_class": segment.flood_hazard_class,
            "hazard_score": segment.flood_hazard_score,
            "source": segment.hazard_layer_source.get("flood_hazard"),
        },
        # Historical GSI evidence, for convenience alongside the zonation
        # result above -- read-only passthrough of the SAME fields
        # GET /segments/{id} already exposes, never recomputed here.
        "historical_landslide_count": segment.historical_landslide_count,
        "nearest_landslide_distance_m": segment.nearest_landslide_distance_m,
    }
