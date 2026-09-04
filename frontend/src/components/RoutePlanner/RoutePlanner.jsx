import { useState } from "react";

const panelStyle = {
  position: "absolute",
  top: "1rem",
  left: "1rem",
  zIndex: 1000,
  background: "white",
  border: "1px solid #ddd",
  borderRadius: "6px",
  padding: "0.75rem 1rem",
  boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
  fontSize: "0.85rem",
  width: "220px",
};

export default function RoutePlanner({ nodes, route, alternativeRoutesAvailable, error, loading, onCalculate }) {
  const [origin, setOrigin] = useState(nodes[0]?.id ?? "");
  const [destination, setDestination] = useState(nodes[nodes.length - 1]?.id ?? "");

  return (
    <div style={panelStyle}>
      <div style={{ fontWeight: 600, marginBottom: "0.5rem" }}>Plan a route</div>

      <label style={{ display: "block", marginBottom: "0.5rem" }}>
        Origin
        <select
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
          style={{ display: "block", width: "100%", marginTop: "0.15rem" }}
        >
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </label>

      <label style={{ display: "block", marginBottom: "0.5rem" }}>
        Destination
        <select
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          style={{ display: "block", width: "100%", marginTop: "0.15rem" }}
        >
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </label>

      <button
        onClick={() => onCalculate(origin, destination)}
        disabled={loading || origin === destination}
        style={{ width: "100%", padding: "0.4rem 0", fontWeight: 600, cursor: "pointer" }}
      >
        {loading ? "CALCULATING…" : "CALCULATE ROUTE"}
      </button>

      {error && <p style={{ color: "#b00020", marginTop: "0.5rem" }}>{error}</p>}

      {route && !error && (
        <div style={{ marginTop: "0.6rem", lineHeight: 1.5 }}>
          <div>Distance: {route.total_distance_km} km</div>
          <div>ETA: {Math.round(route.estimated_travel_time_min)} min</div>
          <div>Segments: {route.segment_ids.length}</div>
          <div style={{ marginTop: "0.4rem", color: "#888", fontSize: "0.75rem" }}>
            {alternativeRoutesAvailable
              ? "Alternative route available."
              : "No alternative route is currently available for this origin/destination pair."}
          </div>
        </div>
      )}
    </div>
  );
}
