import { useState } from "react";

const CARGO_TYPES = [
  { id: "medical", label: "Medicines & Cold-Chain Supplies", icon: "💊" },
  { id: "rations", label: "Food Grains & Rations (FCI)", icon: "🌾" },
  { id: "fuel", label: "Petroleum, Oil & Lubricants (POL)", icon: "⛽" },
  { id: "construction", label: "Construction & Engineering Materials", icon: "🏗️" },
  { id: "general", label: "General Essential Commodities", icon: "📦" },
];

// Route-planning controls: origin/destination + cargo priority + a mode selector
// between the two REAL backend endpoints (see api/client.js).
export default function RoutePlanner({ nodes, mode, onModeChange, loading, error, onCalculate }) {
  const [origin, setOrigin] = useState(nodes[0]?.id ?? "");
  const [destination, setDestination] = useState(nodes[nodes.length - 1]?.id ?? "");
  const [cargoType, setCargoType] = useState(CARGO_TYPES[0].id);

  return (
    <div className="panel">
      <div className="panel__title">Convoy Route Planner</div>

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
          Risk-aware (Recommended)
        </button>
      </div>

      <div className="field-group">
        <label className="field-label" htmlFor="cargo-select">
          Cargo Type &amp; Priority
        </label>
        <select
          id="cargo-select"
          className="field-select"
          value={cargoType}
          onChange={(e) => setCargoType(e.target.value)}
        >
          {CARGO_TYPES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.icon} {c.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field-group">
        <label className="field-label" htmlFor="origin-select">
          Origin Dispatch Point
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
          Destination Station
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
        onClick={() => onCalculate(origin, destination, cargoType)}
        disabled={loading || origin === destination}
      >
        {loading ? "Calculating Safest Route…" : "Plan Convoy Route"}
      </button>

      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
