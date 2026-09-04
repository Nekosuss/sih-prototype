// Application header: title, subtitle, system status, and the two global
// operational actions (Part 13) -- Data Sources (provenance reference) and
// Reset Demo (POST /simulation/reset). "SYSTEM OPERATIONAL" reflects only
// whether the frontend successfully loaded the real network from the
// backend — it is NOT a claim about any trained model (there isn't one;
// see backend/app/data/training_dataset_schema.md for why).
export default function Header({ status, alertCount, onOpenDataSources, onResetDemo, resetting }) {
  const isOperational = status === "operational";

  return (
    <header className="app-header">
      <div className="app-header__title-block">
        <h1 className="app-header__title">NER Logistics Intelligence</h1>
        <p className="app-header__subtitle">Hazard-Aware Route Operations — Guwahati &rarr; Tawang Corridor</p>
      </div>

      <div className="app-header__actions">
        <div className="app-header__status">
          <span className={`status-dot${isOperational ? "" : " status-dot--error"}`} />
          {isOperational ? "Operational" : "Network unavailable"}
        </div>

        {alertCount > 0 && (
          <div className="app-header__status">
            <span className="header-btn__badge">{alertCount}</span>
            {alertCount === 1 ? "active incident" : "active incidents"}
          </div>
        )}

        <button type="button" className="header-btn" onClick={onOpenDataSources}>
          Data Sources
        </button>

        <button type="button" className="header-btn" onClick={onResetDemo} disabled={resetting}>
          {resetting ? "Resetting…" : "Reset Demo"}
        </button>
      </div>
    </header>
  );
}
