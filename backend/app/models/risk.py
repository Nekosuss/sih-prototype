"""
RiskBreakdown / RiskResult — the explainable PROTOTYPE risk score's output
shape (Part 5, see app/core/risk_engine.py for how these are computed).

This replaces the one-line stub this file used to be (a plain comment
describing an intended RiskScore model per ARCHITECTURE.md section 6:
base_risk/weather_factor/incident_factor -> total_risk/level). The shape
here differs from that original sketch in one deliberate way: "hazard" is
split into slope_risk (a real SRTM-DEM terrain feature, see
backend/app/data/README.md "DEM provenance") and historical_landslide_risk
(real GSI observations spatially matched to OSM segments, see
landslide_mapper.py), rather than folding both into one opaque base_risk —
because those are now two independently real, individually explainable
data sources for this corridor, not a single placeholder susceptibility
field.
"""
from enum import Enum

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    low = "low"
    moderate = "moderate"
    high = "high"
    critical = "critical"


class RiskBreakdown(BaseModel):
    """Each component is normalized to [0,1] BEFORE weighting — see
    risk_engine.py's WEIGHT constants for how these combine into
    risk_score. Keeping these unweighted (rather than pre-multiplied) lets
    a caller inspect "how risky is the terrain here, on its own terms"
    independently of how much the current formula happens to weight it."""

    slope_risk: float = Field(ge=0.0, le=1.0)
    historical_landslide_risk: float = Field(ge=0.0, le=1.0)
    weather_risk: float = Field(ge=0.0, le=1.0)
    incident_risk: float = Field(ge=0.0, le=1.0)


class RiskResult(BaseModel):
    """
    The explainable PROTOTYPE risk score for one road segment at the
    moment it was computed — a rule-based, fully auditable estimate, not a
    trained model's calibrated probability. See
    app/core/risk_engine.py's module docstring for the full methodology,
    and backend/app/data/training_dataset_schema.md for exactly what
    additional data (rainfall, event-aligned labels) would be needed
    before any calibrated ML probability could replace this.
    """

    segment_id: str
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    breakdown: RiskBreakdown
    reasons: list[str]
    methodology_note: str = (
        "Explainable prototype risk score: a rule-based weighted combination of "
        "real terrain (DEM slope) and historical-landslide (GSI) data plus "
        "supplied weather/incident context. This is NOT a calibrated probability."
    )
    metadata: dict[str, str] = Field(default_factory=dict)
