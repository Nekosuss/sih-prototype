// Part 15D: display-only helpers for the advisory ML risk signal
// (GET /segments/{id}/ml-risk -- backend/app/core/ml_risk_signal.py).
//
// Deliberately a SEPARATE module from utils/risk.js: that file's
// riskLevelColor/riskLevelLabel mirror the backend's own authoritative
// RiskLevel enum and RISK_LEVEL_THRESHOLDS (app/config.py) -- a real,
// documented classification the backend computes. mlRankingTier() below
// is the opposite: a purely presentational, FRONTEND-INVENTED three-way
// bucketing of the raw ML score, added only so a human can scan the
// number quickly. It carries no operational meaning, is not returned by
// the backend, and is not validated/calibrated in any way -- see
// backend/app/data/ml/artifacts/v2_17_feature/MODEL_CARD.md. It must
// never be styled with the same colored .risk-pill used for the real
// risk_level (see .ml-advisory__tier in styles/index.css -- deliberately
// neutral/outlined).

export function mlRankingTier(score) {
  if (score == null) return null;
  if (score >= 0.66) return "Elevated";
  if (score >= 0.33) return "Moderate";
  return "Low";
}

// A short, human-readable gloss for the more common `reason` strings
// core/ml_risk_signal.py returns -- shown only as a subtle secondary
// detail (a title/tooltip), never as the primary "unavailable" message.
// The dashboard's primary message stays the single, clean
// "ML advisory signal unavailable" regardless of cause (Part 15D section
// 6) -- this is supplementary context only, not a replacement for it.
export function humanizeMlUnavailableReason(reason) {
  if (!reason) return null;
  const lower = reason.toLowerCase();
  if (lower.includes("disabled")) return "The ML signal is currently disabled.";
  if (lower.includes("artifact")) return "The ML model is currently unavailable.";
  if (lower.includes("slope_deg") || lower.includes("elevation_m")) {
    return "Required terrain data is unavailable for this segment.";
  }
  if (lower.includes("as_of_date")) return "Not applicable for the current date.";
  if (lower.includes("road_type") || lower.includes("terrain_type")) {
    return "This segment's category is not supported by the current model.";
  }
  return "The ML signal could not be produced for this segment.";
}
