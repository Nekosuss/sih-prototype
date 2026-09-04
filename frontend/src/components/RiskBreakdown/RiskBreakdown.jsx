import { riskLevelColor, riskLevelLabel } from "../../utils/risk.js";

const COMPONENT_LABELS = [
  ["slope_risk", "Slope Exposure"],
  ["historical_landslide_risk", "Historical Landslide"],
  ["weather_risk", "Weather"],
  ["incident_risk", "Field Incidents"],
];

// The route-level bar uses RouteRiskProfile.aggregate_risk_score (a real
// backend aggregate — see core/routing_engine.py::compute_route_risk_profile,
// NOT a plain average). The per-component breakdown below it, however, is
// NOT something the backend aggregates across a whole route — RiskBreakdown
// only exists per-SEGMENT. Rather than invent a route-wide average in
// JavaScript (which would mean deriving a number the backend never
// computed), this shows the REAL breakdown/reasons of the route's own
// highest-risk segment (riskProfile.max_risk_segment_id) — i.e. "why is
// the worst spot on this route risky", labeled explicitly as such.
export default function RiskBreakdown({ riskProfile, segmentRisks }) {
  if (!riskProfile || !segmentRisks) return null;

  const worst = segmentRisks.find((r) => r.segment_id === riskProfile.max_risk_segment_id);
  const barColor = worst ? riskLevelColor(worst.risk_level) : riskLevelColor("moderate");

  return (
    <div className="panel">
      <div className="panel__title">Route Risk</div>

      <div className="risk-headline">
        <div className="risk-bar-track">
          <div
            className="risk-bar-fill"
            style={{ width: `${Math.min(100, riskProfile.aggregate_risk_score * 100)}%`, background: barColor }}
          />
        </div>
        <span className="risk-headline__score">{riskProfile.aggregate_risk_score.toFixed(2)}</span>
        {worst && (
          <span className="risk-pill" style={{ background: barColor }}>
            {riskLevelLabel(worst.risk_level)}
          </span>
        )}
      </div>

      {worst && (
        <>
          <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
            Component breakdown of the route's highest-risk segment ({worst.segment_id}):
          </div>
          {COMPONENT_LABELS.map(([key, label]) => (
            <div className="risk-component-row" key={key}>
              <span className="risk-component-row__label">{label}</span>
              <div className="risk-component-row__track">
                <div
                  className="risk-component-row__fill"
                  style={{ width: `${Math.min(100, worst.breakdown[key] * 100)}%` }}
                />
              </div>
              <span className="risk-component-row__value">{worst.breakdown[key].toFixed(2)}</span>
            </div>
          ))}

          {worst.reasons.length > 0 && (
            <ul className="reasons-list">
              {worst.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          )}
        </>
      )}

      <div className="methodology-note">{worst?.methodology_note || riskProfile.methodology_note}</div>
    </div>
  );
}
