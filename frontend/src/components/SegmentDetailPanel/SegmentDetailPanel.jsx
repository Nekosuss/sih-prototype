import { useEffect, useState } from "react";
import { getSegmentHazardLayers, getSegmentMlRisk, getSegmentWeather } from "../../api/client.js";
import { riskLevelColor, riskLevelLabel } from "../../utils/risk.js";
import { humanizeMlUnavailableReason, mlRankingTier } from "../../utils/mlRisk.js";

// Part 11: click-to-inspect combined segment detail. Fetches ONLY for the
// one clicked segment (never all ~2,964 -- see MapView.jsx's click
// handler) so this never turns into a mass of background requests. Combines
// Part 11's static landslide/flood hazard-zonation result
// (GET /hazards/segments/{id}) with Part 10's real rainfall + full Part 5
// risk breakdown (GET /weather/segments/{id}) -- two independent endpoints,
// composed here rather than duplicating either's logic in JavaScript.
//
// Part 15D adds a THIRD, fully independent fetch: the advisory ML risk
// signal (GET /segments/{id}/ml-risk). It is deliberately NOT joined into
// the Promise.all above -- an ML failure/slow response must never delay or
// break the authoritative hazard/weather/risk content above it, and vice
// versa. See the `ml`/`mlLoading`/`mlError` state below.
function formatClass(status, hazardClass) {
  if (status !== "ok") return "not available (no official layer)";
  return hazardClass;
}

export default function SegmentDetailPanel({ segmentId, segmentName, onClose }) {
  const [hazard, setHazard] = useState(null);
  const [weather, setWeather] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  // Part 15D: the advisory ML signal, fetched independently -- see module
  // docstring above. `ml` is the raw MLRiskSignal (available may be true
  // or false); `mlError` is only set for an actual request failure (e.g.
  // network error), never for a normal available:false response.
  const [ml, setMl] = useState(null);
  const [mlLoading, setMlLoading] = useState(true);
  const [mlError, setMlError] = useState(false);

  useEffect(() => {
    if (!segmentId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getSegmentHazardLayers(segmentId), getSegmentWeather(segmentId)])
      .then(([hazardResult, weatherResult]) => {
        if (cancelled) return;
        setHazard(hazardResult);
        setWeather(weatherResult);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [segmentId]);

  // Part 15D: one fetch per segment selection, not polled -- see
  // api/client.js's getSegmentMlRisk docstring. Isolated try/catch so a
  // failure here (network error, unexpected backend error) never touches
  // `error`/`loading` above and never breaks the rest of this panel.
  useEffect(() => {
    if (!segmentId) return;
    let cancelled = false;
    setMlLoading(true);
    setMlError(false);
    setMl(null);
    getSegmentMlRisk(segmentId)
      .then((result) => {
        if (!cancelled) setMl(result);
      })
      .catch(() => {
        if (!cancelled) setMlError(true);
      })
      .finally(() => {
        if (!cancelled) setMlLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [segmentId]);

  if (!segmentId) return null;

  return (
    <div className="panel">
      <div className="panel__title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Road Segment{segmentName ? `: ${segmentName}` : ""}</span>
        <button type="button" className="overlay-panel__close" style={{ padding: 0 }} onClick={onClose} aria-label="Close segment detail">
          &times;
        </button>
      </div>

      {loading && <div className="empty-state" style={{ padding: "0.6rem 0" }}>Loading&hellip;</div>}
      {error && <div className="form-error">{error}</div>}

      {hazard && weather && !loading && (
        <>
          <div className="compare-card">
            <div className="compare-card__row">
              Slope: <strong>{weather.risk.metadata.slope_deg ? `${parseFloat(weather.risk.metadata.slope_deg).toFixed(1)}°` : "n/a"}</strong>
            </div>
            <div className="compare-card__row">
              Historical landslides: <strong>{hazard.historical_landslide_count}</strong>
              {hazard.nearest_landslide_distance_m != null && ` (nearest ${Math.round(hazard.nearest_landslide_distance_m)}m)`}
            </div>
            <div className="compare-card__row">
              Landslide hazard: <strong>{formatClass(hazard.landslide_hazard.status, hazard.landslide_hazard.hazard_class)}</strong>
              {hazard.landslide_hazard.hazard_score != null && ` (score ${hazard.landslide_hazard.hazard_score.toFixed(2)})`}
            </div>
            <div className="compare-card__row">
              Flood hazard: <strong>{formatClass(hazard.flood_hazard.status, hazard.flood_hazard.hazard_class)}</strong>
              {hazard.flood_hazard.hazard_score != null && ` (score ${hazard.flood_hazard.hazard_score.toFixed(2)})`}
            </div>
            <div className="compare-card__row">
              Rainfall ({weather.observation_date}):{" "}
              <strong>{weather.is_real_observation ? `${weather.rainfall_mm.toFixed(1)} mm/day` : "not available"}</strong>
            </div>
            <div className="compare-card__row">
              Weather factor: <strong>{weather.weather_factor != null ? weather.weather_factor.toFixed(2) : "n/a"}</strong>
            </div>
          </div>

          <div className="compare-card__label" style={{ marginTop: "0.7rem", marginBottom: "0.2rem" }}>
            Authoritative &middot; Current Segment Risk
          </div>
          <div className="risk-headline" style={{ marginTop: "0.2rem" }}>
            <span className="risk-headline__score">{weather.risk.risk_score.toFixed(2)}</span>
            <span className="risk-pill" style={{ background: riskLevelColor(weather.risk.risk_level) }}>
              {riskLevelLabel(weather.risk.risk_level)}
            </span>
          </div>

          <div className="methodology-note">
            Landslide/flood hazard zonation: {hazard.landslide_hazard.source || hazard.flood_hazard.source || "APSAC (official layer not locally available)"}.
            Overall risk is the explainable prototype score -- not a calibrated probability. This score, its
            weights, and thresholds are the sole basis for routing, thresholds, and PROCEED/REROUTE/SUSPEND
            decisions.
          </div>

          {/* Part 15D: advisory-only ML risk signal (GET /segments/{id}/ml-risk).
              Deliberately below a dashed separator and never using the colored
              .risk-pill above -- see utils/mlRisk.js's module docstring for why
              this must stay visually distinguishable from the authoritative
              score above, and ml_dashboard_part15d.md for the full rationale. */}
          <div className="ml-advisory">
            <div className="compare-card__label" style={{ marginBottom: "0.2rem" }}>
              Advisory &middot; ML Risk Signal
            </div>

            {mlLoading && <div className="ml-advisory__muted">Loading ML signal&hellip;</div>}

            {!mlLoading && mlError && <div className="ml-advisory__muted">ML advisory signal unavailable</div>}

            {!mlLoading && !mlError && ml && !ml.available && (
              <div className="ml-advisory__muted" title={humanizeMlUnavailableReason(ml.reason) || undefined}>
                ML advisory signal unavailable
              </div>
            )}

            {!mlLoading && !mlError && ml && ml.available && (
              <>
                <div className="ml-advisory__row">
                  <span className="ml-advisory__score">{ml.score.toFixed(3)}</span>
                  <span className="ml-advisory__tier">{mlRankingTier(ml.score)}</span>
                </div>
                <div className="ml-advisory__meta">
                  Model: <strong>{ml.model_version || "n/a"}</strong> &middot; Status: <strong>Advisory</strong>
                </div>
              </>
            )}

            <div className="ml-advisory__note">
              Prototype ML ranking signal; not a calibrated probability and not used to determine routing or
              safety decisions.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
