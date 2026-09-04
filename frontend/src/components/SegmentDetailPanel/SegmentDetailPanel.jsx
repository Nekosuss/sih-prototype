import { useEffect, useState } from "react";
import { getSegmentHazardLayers, getSegmentWeather } from "../../api/client.js";
import { riskLevelColor, riskLevelLabel } from "../../utils/risk.js";

// Part 11: click-to-inspect combined segment detail. Fetches ONLY for the
// one clicked segment (never all ~2,964 -- see MapView.jsx's click
// handler) so this never turns into a mass of background requests. Combines
// Part 11's static landslide/flood hazard-zonation result
// (GET /hazards/segments/{id}) with Part 10's real rainfall + full Part 5
// risk breakdown (GET /weather/segments/{id}) -- two independent endpoints,
// composed here rather than duplicating either's logic in JavaScript.
function formatClass(status, hazardClass) {
  if (status !== "ok") return "not available (no official layer)";
  return hazardClass;
}

export default function SegmentDetailPanel({ segmentId, segmentName, onClose }) {
  const [hazard, setHazard] = useState(null);
  const [weather, setWeather] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

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

          <div className="risk-headline" style={{ marginTop: "0.6rem" }}>
            <span className="risk-headline__score">{weather.risk.risk_score.toFixed(2)}</span>
            <span className="risk-pill" style={{ background: riskLevelColor(weather.risk.risk_level) }}>
              {riskLevelLabel(weather.risk.risk_level)}
            </span>
          </div>

          <div className="methodology-note">
            Landslide/flood hazard zonation: {hazard.landslide_hazard.source || hazard.flood_hazard.source || "APSAC (official layer not locally available)"}.
            Overall risk is the explainable prototype score -- not a calibrated probability.
          </div>
        </>
      )}
    </div>
  );
}
