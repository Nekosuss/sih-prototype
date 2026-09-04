import { riskLevelColor, riskLevelLabel } from "../../utils/risk.js";

function formatDuration(minutes) {
  const total = Math.round(minutes);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// Route-level "Risk Level" badge is NOT a frontend threshold calculation —
// it's the risk_level the backend already assigned to the route's own
// max-risk segment (RiskResult.risk_level), looked up by
// riskProfile.max_risk_segment_id. The backend is still the sole source of
// the low/moderate/high/critical boundary; this is just an array lookup.
function maxSegmentRiskLevel(riskProfile, segmentRisks) {
  if (!riskProfile || !segmentRisks) return null;
  const match = segmentRisks.find((r) => r.segment_id === riskProfile.max_risk_segment_id);
  return match ? match.risk_level : null;
}

export default function RouteSummary({ route, routeTypeLabel, riskProfile, segmentRisks, alternativeRoutesAvailable }) {
  if (!route) return null;

  const level = maxSegmentRiskLevel(riskProfile, segmentRisks);
  const highCriticalCount = riskProfile
    ? (riskProfile.segment_count_by_risk_level.high || 0) + (riskProfile.segment_count_by_risk_level.critical || 0)
    : null;

  return (
    <div className="panel">
      <div className="panel__title">Route Summary</div>
      <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none", marginBottom: "0.55rem" }}>
        {routeTypeLabel}
      </div>
      <div className="stat-grid">
        <div className="stat-tile">
          <span className="stat-tile__label">Distance</span>
          <span className="stat-tile__value">{route.total_distance_km} km</span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile__label">ETA</span>
          <span className="stat-tile__value">{formatDuration(route.estimated_travel_time_min)}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile__label">Segments</span>
          <span className="stat-tile__value">{route.segment_ids.length}</span>
        </div>

        {riskProfile && (
          <>
            <div className="stat-tile">
              <span className="stat-tile__label">Prototype Risk Score</span>
              <span className="stat-tile__value">{riskProfile.aggregate_risk_score.toFixed(2)}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">Risk Level</span>
              <span className="stat-tile__value stat-tile__value--risk">
                {level ? (
                  <span className="risk-pill" style={{ background: riskLevelColor(level) }}>
                    {riskLevelLabel(level)}
                  </span>
                ) : (
                  "—"
                )}
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">Maximum Segment Risk</span>
              <span className="stat-tile__value">{riskProfile.max_segment_risk.toFixed(2)}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">High/Critical Segments</span>
              <span className="stat-tile__value">{highCriticalCount}</span>
            </div>
          </>
        )}
      </div>

      {alternativeRoutesAvailable != null && (
        <div className="methodology-note">
          {alternativeRoutesAvailable
            ? "A path-disjoint alternative exists for this origin/destination in the real road network."
            : "No path-disjoint alternative exists for this origin/destination in the real road network."}
        </div>
      )}
    </div>
  );
}
