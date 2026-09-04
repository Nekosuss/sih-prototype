import { useState } from "react";

// Route-planning controls: origin/destination + a mode selector between the
// two REAL backend endpoints (see api/client.js) — no routing logic lives
// here, this component only collects input and calls onCalculate.
export default function RoutePlanner({ nodes, mode, onModeChange, loading, error, onCalculate }) {
  const [origin, setOrigin] = useState(nodes[0]?.id ?? "");
  const [destination, setDestination] = useState(nodes[nodes.length - 1]?.id ?? "");

  return (
    <div className="panel">
      <div className="panel__title">Route Planner</div>

      <div className="mode-toggle">
        <button
          type="button"
          className={`mode-toggle__option${mode === "fastest" ? " mode-toggle__option--active" : ""}`}
          onClick={() => onModeChange("fastest")}
          title="Shortest travel time only, no hazard/risk weighting"
        >
          Fastest
        </button>
        <button
          type="button"
          className={`mode-toggle__option${mode === "risk-aware" ? " mode-toggle__option--active" : ""}`}
          onClick={() => onModeChange("risk-aware")}
          title="Weighs terrain, historical landslides, weather, and incidents against travel time"
        >
          Risk-aware
        </button>
      </div>

      <div className="field-group">
        <label className="field-label" htmlFor="origin-select">
          Origin
        </label>
        <select
          id="origin-select"
          className="field-select"
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
        >
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field-group">
        <label className="field-label" htmlFor="destination-select">
          Destination
        </label>
        <select
          id="destination-select"
          className="field-select"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
        >
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </div>

      <button
        type="button"
        className="btn-primary"
        onClick={() => onCalculate(origin, destination)}
        disabled={loading || origin === destination}
      >
        {loading ? "Calculating…" : "Calculate route"}
      </button>

      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
