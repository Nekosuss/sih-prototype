import { useEffect, useState } from "react";
import { createFieldReport, resolveFieldReport } from "../../api/client.js";
import { DECISION_ALERT_STYLE, DECISION_ICON, DECISION_LABELS, FIELD_INCIDENT_TYPE_LABEL } from "../../utils/risk.js";

const INCIDENT_TYPES = Object.keys(FIELD_INCIDENT_TYPE_LABEL);
const SEVERITIES = ["minor", "major", "blocking"];

function formatRouteImpact(decision) {
  if (!decision) return null;
  return {
    icon: DECISION_ICON[decision.outcome],
    label: DECISION_LABELS[decision.outcome],
    style: DECISION_ALERT_STYLE[decision.outcome],
    reason: decision.reason,
  };
}

// Part 12/13: field-worker incident report submission. Every submission is
// matched to a REAL OSM segment and fed into the SAME hazard/risk/reroute
// pipeline Part 8's demo hazards use (see
// backend/app/core/field_report_service.py) -- this component computes
// nothing itself, it only calls POST /field-reports and displays the real
// backend response. `origin`/`destination`/`currentRouteId` (the CURRENTLY
// DISPLAYED route, if any) are optional -- when present, the backend also
// returns this report's route impact in the same response.
//
// The list of active reports (with Resolve actions) lives in
// AlertCenter.jsx, alongside active demo hazards -- ONE consolidated
// operational list rather than a second one duplicated here (Part 13).
export default function FieldReportPanel({
  origin,
  destination,
  currentRouteId,
  pickedLocation,
  onStartPicking,
  onDecision,
  onReportSubmitted,
  onReportResolved,
}) {
  const [incidentType, setIncidentType] = useState(INCIDENT_TYPES[0]);
  const [severity, setSeverity] = useState("major");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [description, setDescription] = useState("");
  const [reporterName, setReporterName] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [lastResult, setLastResult] = useState(null); // FieldReportResponse | null

  useEffect(() => {
    if (pickedLocation) {
      setLatitude(String(pickedLocation.lat.toFixed(6)));
      setLongitude(String(pickedLocation.lng.toFixed(6)));
    }
  }, [pickedLocation]);

  function routeContext() {
    return { origin, destination, previousRouteId: currentRouteId };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const lat = parseFloat(latitude);
      const lng = parseFloat(longitude);
      const result = await createFieldReport(
        { incidentType, severity, latitude: lat, longitude: lng, description, reporterName: reporterName || undefined },
        routeContext()
      );
      setLastResult(result);
      onDecision?.(result.route_decision || null);
      onReportSubmitted?.(result.report);
      setDescription("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleResolveLastReport() {
    if (!lastResult) return;
    setBusy(true);
    setError(null);
    try {
      const result = await resolveFieldReport(lastResult.report.id, routeContext());
      setLastResult(result);
      onDecision?.(result.route_decision || null);
      onReportResolved?.(result.report);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const routeImpact = formatRouteImpact(lastResult?.route_decision);
  const isResolved = lastResult?.report.status === "resolved";

  return (
    <div className="panel">
      <div className="panel__title">Field Report</div>
      <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none", marginBottom: "0.6rem" }}>
        Real field-worker observation — not a verified GSI/APSAC/IMD record, and not a demo simulation. Feeds the
        same hazard/risk/reroute pipeline as every other real input.
      </div>

      <form onSubmit={handleSubmit}>
        <div className="field-group">
          <label className="field-label" htmlFor="fr-incident-type">
            Incident type
          </label>
          <select
            id="fr-incident-type"
            className="field-select"
            value={incidentType}
            onChange={(e) => setIncidentType(e.target.value)}
          >
            {INCIDENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {FIELD_INCIDENT_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </div>

        <div className="field-group">
          <label className="field-label" htmlFor="fr-severity">
            Severity
          </label>
          <select id="fr-severity" className="field-select" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>

        <div className="field-group" style={{ display: "flex", gap: "0.5rem" }}>
          <div style={{ flex: 1 }}>
            <label className="field-label" htmlFor="fr-lat">
              Latitude
            </label>
            <input
              id="fr-lat"
              className="field-select"
              type="text"
              inputMode="decimal"
              placeholder="27.0137"
              value={latitude}
              onChange={(e) => setLatitude(e.target.value)}
              required
            />
          </div>
          <div style={{ flex: 1 }}>
            <label className="field-label" htmlFor="fr-lng">
              Longitude
            </label>
            <input
              id="fr-lng"
              className="field-select"
              type="text"
              inputMode="decimal"
              placeholder="92.6358"
              value={longitude}
              onChange={(e) => setLongitude(e.target.value)}
              required
            />
          </div>
        </div>

        <button
          type="button"
          className="btn-secondary"
          onClick={onStartPicking}
          style={{ marginBottom: "0.65rem" }}
          title="Click a point on the map to fill in these coordinates"
        >
          Use map location
        </button>

        <div className="field-group">
          <label className="field-label" htmlFor="fr-description">
            Description
          </label>
          <textarea
            id="fr-description"
            className="field-select"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What did you observe?"
            required
          />
        </div>

        <div className="field-group">
          <label className="field-label" htmlFor="fr-reporter">
            Reporter name (optional)
          </label>
          <input
            id="fr-reporter"
            className="field-select"
            type="text"
            value={reporterName}
            onChange={(e) => setReporterName(e.target.value)}
          />
        </div>

        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Submitting…" : "Submit field report"}
        </button>
      </form>

      {error && <div className="form-error">{error}</div>}

      {lastResult && (
        <div className="compare-card" style={{ marginTop: "0.65rem" }}>
          <div className="compare-card__row">
            <strong>{isResolved ? "Report resolved" : "Report received"}</strong>
          </div>
          <div className="compare-card__row">
            Location matched: <strong>{lastResult.report.segment_name || lastResult.report.segment_id}</strong>{" "}
            ({Math.round(lastResult.report.distance_to_road_m)} m from road)
          </div>
          <div className="compare-card__row">
            {FIELD_INCIDENT_TYPE_LABEL[lastResult.report.incident_type]} —{" "}
            {lastResult.report.severity[0].toUpperCase() + lastResult.report.severity.slice(1)}
          </div>
          {!isResolved && (
            <div className="compare-card__row">
              Current risk score: <strong>{lastResult.current_risk.risk_score.toFixed(2)}</strong>
            </div>
          )}
          {!isResolved && lastResult.report.possible_duplicate && (
            <div className="compare-card__row">
              <span className="badge badge--duplicate">Possible duplicate</span>
            </div>
          )}
          {!isResolved && routeImpact && (
            <div className={`alert alert--${routeImpact.style}`} style={{ marginTop: "0.5rem" }}>
              <span className="alert__icon">{routeImpact.icon}</span>
              <span>{routeImpact.label}</span>
            </div>
          )}
          {!isResolved && !routeImpact && origin && destination && (
            <div className="alert alert--ok" style={{ marginTop: "0.5rem" }}>
              <span className="alert__icon">&#10003;</span>
              <span>Route unaffected</span>
            </div>
          )}

          {!isResolved && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "0.5rem" }}
              onClick={handleResolveLastReport}
              disabled={busy}
            >
              {busy ? "Resolving…" : "Resolve this report"}
            </button>
          )}
        </div>
      )}

      <div className="methodology-note">
        Offline queue/synchronization is reserved for future production implementation — this prototype requires
        network connectivity to submit a report.
      </div>
    </div>
  );
}
