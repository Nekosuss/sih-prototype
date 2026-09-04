import { useEffect, useState } from "react";
import {
  clearHazard,
  evaluateDisruption,
  getSegmentRiskAware,
  simulateHazard,
} from "../../api/client.js";
import {
  DECISION_ALERT_STYLE,
  DECISION_ICON,
  DECISION_LABELS,
  HAZARD_TYPE_LABEL,
} from "../../utils/risk.js";

const HAZARD_TYPES = [
  { value: "heavy_rain", label: "Simulated Heavy Rain" },
  { value: "road_blockage", label: "Simulated Road Blockage" },
  { value: "landslide", label: "Simulated Landslide" },
];

const SEVERITIES = ["minor", "major", "blocking"];

// Part 8: DEMO SIMULATION panel. Triggers a deterministic simulated hazard
// on a REAL segment of the currently displayed route, then asks the
// backend (POST /routes/evaluate-disruption) what should happen --
// CONTINUE / REROUTE / SUSPEND. Every id, risk value, and decision shown
// here comes from a backend response; nothing is computed in this
// component. `routeSegments` is the list of {id, name} for the CURRENTLY
// DISPLAYED route only -- never the whole 2,964-segment network -- so the
// dropdown always reflects real, currently-relevant segments.
export default function HazardControl({ routeSegments, origin, destination, currentRouteId, onDecision }) {
  const [segmentId, setSegmentId] = useState(routeSegments[0]?.id ?? "");
  const [hazardType, setHazardType] = useState(HAZARD_TYPES[0].value);
  const [severity, setSeverity] = useState("major");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const [activeHazard, setActiveHazard] = useState(null); // HazardEvent | null
  const [beforeRisk, setBeforeRisk] = useState(null); // RiskResult | null
  const [afterRisk, setAfterRisk] = useState(null); // RiskResult | null
  const [decision, setDecision] = useState(null); // RouteDecision | null

  useEffect(() => {
    // Keep the selection valid as the underlying route changes.
    if (!routeSegments.some((s) => s.id === segmentId)) {
      setSegmentId(routeSegments[0]?.id ?? "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSegments]);

  async function handleTrigger() {
    if (!segmentId) return;
    setBusy(true);
    setError(null);
    try {
      const before = await getSegmentRiskAware(segmentId);
      const hazard = await simulateHazard(hazardType, severity, [segmentId]);
      const after = await getSegmentRiskAware(segmentId);
      const nextDecision = await evaluateDisruption(origin, destination, currentRouteId);

      setBeforeRisk(before);
      setAfterRisk(after);
      setActiveHazard(hazard);
      setDecision(nextDecision);
      onDecision(nextDecision);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    if (!activeHazard) return;
    setBusy(true);
    setError(null);
    try {
      await clearHazard(activeHazard.id);
      const restored = await evaluateDisruption(origin, destination);
      setActiveHazard(null);
      setBeforeRisk(null);
      setAfterRisk(null);
      setDecision(null);
      onDecision(null);
      void restored; // the sidebar's own route recalculation is the source of truth for "restored" display
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const segmentLabel = (id) => routeSegments.find((s) => s.id === id)?.name || id;

  return (
    <div className="panel">
      <div className="panel__title">Demo Hazard Simulation</div>
      <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none", marginBottom: "0.6rem" }}>
        Deterministic simulated input for demonstration only — not live weather or a field report.
      </div>

      {!activeHazard ? (
        <>
          <div className="field-group">
            <label className="field-label" htmlFor="hazard-segment-select">
              Route segment
            </label>
            <select
              id="hazard-segment-select"
              className="field-select"
              value={segmentId}
              onChange={(e) => setSegmentId(e.target.value)}
              disabled={routeSegments.length === 0}
            >
              {routeSegments.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || s.id}
                </option>
              ))}
            </select>
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="hazard-type-select">
              Hazard type
            </label>
            <select
              id="hazard-type-select"
              className="field-select"
              value={hazardType}
              onChange={(e) => setHazardType(e.target.value)}
            >
              {HAZARD_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="hazard-severity-select">
              Severity
            </label>
            <select
              id="hazard-severity-select"
              className="field-select"
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
            >
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s[0].toUpperCase() + s.slice(1)}
                </option>
              ))}
            </select>
          </div>

          <button type="button" className="btn-primary" onClick={handleTrigger} disabled={busy || !segmentId}>
            {busy ? "Triggering…" : "Trigger hazard"}
          </button>
        </>
      ) : (
        <>
          <div className="alert alert--warn" style={{ marginBottom: "0.6rem" }}>
            <span className="alert__icon">⚠</span>
            <span>Hazard active</span>
          </div>

          <div className="compare-card" style={{ marginBottom: "0.6rem" }}>
            <div className="compare-card__row">
              <strong>{HAZARD_TYPE_LABEL[activeHazard.type]}</strong> ({activeHazard.severity})
            </div>
            <div className="compare-card__row">Affected segment: {segmentLabel(activeHazard.affected_segment_ids[0])}</div>
            {beforeRisk && afterRisk && (
              <div className="compare-card__row">
                Risk: <strong>{beforeRisk.risk_score.toFixed(2)}</strong> &rarr;{" "}
                <strong style={{ color: "var(--status-high)" }}>{afterRisk.risk_score.toFixed(2)}</strong>
              </div>
            )}
          </div>

          {decision && (
            <div className={`alert alert--${DECISION_ALERT_STYLE[decision.outcome]}`} style={{ marginBottom: "0.6rem" }}>
              <span className="alert__icon">{DECISION_ICON[decision.outcome]}</span>
              <span>{DECISION_LABELS[decision.outcome]}</span>
            </div>
          )}

          {decision && (
            <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
              {decision.reason}
            </div>
          )}

          <button type="button" className="btn-primary" onClick={handleClear} disabled={busy} style={{ marginTop: "0.6rem" }}>
            {busy ? "Clearing…" : "Clear hazard"}
          </button>
        </>
      )}

      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
