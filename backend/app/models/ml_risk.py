"""
Part 15B: MLRiskSignal -- the output shape of the isolated ML inference
service (core/ml_risk_signal.py). Deliberately separate from
models/risk.py's RiskResult: this is an ADVISORY, EXPERIMENTAL signal from
an uncalibrated prototype Random Forest (see
backend/app/data/ml/artifacts/v2_17_feature/MODEL_CARD.md), not part of
the authoritative explainable risk score. RiskResult/RiskBreakdown are not
imported, extended, or modified by this file -- their semantics are
completely unchanged.

`score` (when `available` is True) is the model's raw predict_proba()
output for the positive class, already bounded in [0,1] by construction --
NOT a calibrated probability. Neither the saved v1 nor v2 model was fit
with any calibration step (Platt scaling, isotonic regression, etc.) and
no trustworthy base rate exists in the training data to calibrate against
(zero confirmed-negative labels -- see MODEL_CARD.md's "Labels" section).
Approved terms for this value, everywhere it's surfaced: "ML risk signal"
/ "ML ranking score". Never: "probability of landslide", "calibrated
probability", "likelihood percentage".
"""
from typing import Optional

from pydantic import BaseModel, Field


class MLRiskSignal(BaseModel):
    """
    `available=False` whenever the ML service could not honestly produce a
    signal for this request -- disabled by configuration, artifact
    missing/corrupt, manifest/schema mismatch, an input feature that
    cannot be honestly computed for this segment/date, an inference
    exception, or an invalid model output. `reason` always says which.

    Callers must treat `available=False` as "no ML signal for this request
    right now" -- never substitute a fabricated score, and never let its
    absence change any other computation. This mirrors how
    risk_engine.py already treats a 0 weather_factor/incident_factor
    default as "no signal supplied," never as a disguised "confirmed safe."
    """

    available: bool
    score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Raw model predict_proba() output for the positive class. "
                    "NOT a calibrated probability. Only meaningful (see "
                    "MODEL_CARD.md) as a relative ranking signal, not as an "
                    "absolute magnitude.",
    )
    model_version: Optional[str] = Field(
        default=None, description="The loaded artifact's model_manifest.json::experiment_id."
    )
    feature_schema_version: Optional[str] = Field(
        default=None,
        description="Short fingerprint of the artifact's feature_names_in_order list, "
                    "so a caller can detect when two signals came from different schemas.",
    )
    reason: str = Field(description="Always populated -- 'ok' on success, or a plain-language "
                                     "explanation of why available=False.")
    methodology_note: str = (
        "Experimental ML ranking score from an uncalibrated prototype Random Forest "
        "(backend/app/data/ml/artifacts/v2_17_feature/MODEL_CARD.md). This is NOT a "
        "calibrated probability of a landslide occurring, and is NOT used anywhere in "
        "the production risk score, routing cost, hard unsafe threshold, or "
        "PROCEED/REROUTE/SUSPEND decisions -- see "
        "backend/app/data/ml/ml_integration_design_part15.md."
    )
