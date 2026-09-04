import { useEffect, useState } from "react";
import { getCorridorWeather } from "../../api/client.js";

// Part 10: REAL IMD gridded rainfall (0.25 x 0.25 deg), NOT a live feed or
// forecast -- see backend/app/data/rainfall_loader.py. This is an
// ADDITIONAL, independent input path alongside Part 8's simulated hazard
// controls (HazardControl.jsx) -- it doesn't replace or control them.
// Every number shown here is a real value from GET /weather/corridor;
// nothing is computed in this component.
export default function WeatherControls() {
  const [date, setDate] = useState(""); // "" = let the backend pick its default demo observation date
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getCorridorWeather(date || undefined)
      .then((data) => {
        if (!cancelled) setSummary(data);
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
  }, [date]);

  return (
    <div className="panel">
      <div className="panel__title">Historical Rainfall (IMD)</div>
      <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none", marginBottom: "0.6rem" }}>
        Historical IMD gridded rainfall (0.25&deg; grid) assigned to the nearest grid cell of each location — this is
        observed data, not a live feed or forecast.
      </div>

      <div className="field-group">
        <label className="field-label" htmlFor="rainfall-date-input">
          Observation Date
        </label>
        <input
          id="rainfall-date-input"
          type="date"
          className="field-select"
          min="2023-01-01"
          max="2023-12-31"
          value={date}
          placeholder={summary?.observation_date}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>

      {loading && <div className="empty-state" style={{ padding: "0.6rem 0" }}>Loading rainfall data&hellip;</div>}
      {error && <div className="form-error">{error}</div>}

      {summary && !loading && (
        <>
          <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
            Observation date: <strong>{summary.observation_date}</strong> &middot; Source: IMD
          </div>

          {summary.locations.map((loc) => (
            <div className="risk-component-row" key={loc.name}>
              <span className="risk-component-row__label">{loc.name}</span>
              <div className="risk-component-row__track">
                <div
                  className="risk-component-row__fill"
                  style={{ width: `${Math.min(100, (loc.weather_factor || 0) * 100)}%` }}
                />
              </div>
              <span className="risk-component-row__value">
                {loc.status === "ok" ? `${loc.rainfall_mm.toFixed(1)}mm` : "no data"}
              </span>
            </div>
          ))}

          <div className="methodology-note" style={{ marginBottom: 0 }}>
            {summary.high_rainfall_segment_count > 0
              ? `${summary.high_rainfall_segment_count} real corridor segment(s) at/above a moderate-or-higher rainfall-driven weather factor (>= ${summary.high_rainfall_threshold_weather_factor}) on this date.`
              : "No real corridor segment reaches a moderate-or-higher rainfall-driven weather factor on this date."}
          </div>

          {summary.high_rainfall_segments.length > 0 && (
            <ul className="reasons-list">
              {summary.high_rainfall_segments.slice(0, 5).map((seg) => (
                <li key={seg.segment_id}>
                  {seg.name || seg.segment_id}: {seg.rainfall_mm.toFixed(1)}mm (weather factor {seg.weather_factor.toFixed(2)})
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
