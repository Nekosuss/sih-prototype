function formatDuration(minutes) {
  const total = Math.round(minutes);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// Side-by-side comparison of the fastest route vs. the risk-aware
// recommendation, both computed by ONE backend call
// (/routes/calculate-risk-aware — see api/client.js). Every number here
// comes straight from that response; nothing is computed client-side.
export default function RouteComparison({ result }) {
  if (!result) return null;

  const { fastest_route, fastest_route_risk, recommended_route, recommended_route_risk } = result;
  const isSameRoute = recommended_route && recommended_route.node_ids.join() === fastest_route.node_ids.join();

  return (
    <div className="panel">
      <div className="panel__title">Fastest vs. Risk-Aware Comparison</div>

      <div className="compare-grid">
        <div className="compare-card">
          <div className="compare-card__label">Fastest Route</div>
          <div className="compare-card__row">
            <strong>{fastest_route.total_distance_km}</strong> km
          </div>
          <div className="compare-card__row">
            <strong>{formatDuration(fastest_route.estimated_travel_time_min)}</strong>
          </div>
          <div className="compare-card__row">
            Risk <strong>{fastest_route_risk.aggregate_risk_score.toFixed(2)}</strong>
          </div>
        </div>

        <div className={`compare-card${recommended_route && !isSameRoute ? " compare-card--recommended" : ""}`}>
          <div className="compare-card__label">
            {isSameRoute ? "Risk-Aware Route (same as fastest)" : "Risk-Aware Route"}
          </div>
          {recommended_route ? (
            <>
              <div className="compare-card__row">
                <strong>{recommended_route.total_distance_km}</strong> km
              </div>
              <div className="compare-card__row">
                <strong>{formatDuration(recommended_route.estimated_travel_time_min)}</strong>
              </div>
              <div className="compare-card__row">
                Risk <strong>{recommended_route_risk.aggregate_risk_score.toFixed(2)}</strong>
              </div>
            </>
          ) : (
            <div className="compare-card__row" style={{ color: "var(--risk-high)" }}>
              No feasible route avoids every hard-unsafe segment.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
