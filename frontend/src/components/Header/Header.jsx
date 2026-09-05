// Application header: title, subtitle, role-based workspace navigation,
// system status, and global operational actions (Data Sources, Reset Demo).
const WORKSPACES = [
  { id: "dispatch", label: "Fleet Dispatch", icon: "🚚", description: "Route planning, cargo priority, convoy tracking & reroutes" },
  { id: "command", label: "Command Center", icon: "🛡️", description: "Regional corridor health, bottleneck analytics & alerts" },
  { id: "field", label: "Field Reporting", icon: "📍", description: "Mobile-ready ground incident logging & obstacle snapping" },
  { id: "lab", label: "Simulation Lab", icon: "🧪", description: "Disruption stress-testing, weather simulation & demo reset" },
];

export default function Header({
  activeWorkspace,
  onWorkspaceChange,
  status,
  alertCount,
  onOpenDataSources,
  onResetDemo,
  resetting,
}) {
  const isOperational = status === "operational";

  return (
    <header className="app-header">
      <div className="app-header__title-block">
        <h1 className="app-header__title">NER Logistics Intelligence</h1>
        <p className="app-header__subtitle">Smart Accessibility Platform &middot; Guwahati &rarr; Tawang Corridor</p>
      </div>

      <nav className="app-header__nav" aria-label="Workspaces">
        {WORKSPACES.map((ws) => {
          const isActive = activeWorkspace === ws.id;
          const showBadge = (ws.id === "command" || ws.id === "field") && alertCount > 0;

          return (
            <button
              key={ws.id}
              type="button"
              className={`nav-tab${isActive ? " nav-tab--active" : ""}`}
              onClick={() => onWorkspaceChange(ws.id)}
              title={ws.description}
            >
              <span className="nav-tab__icon">{ws.icon}</span>
              <span className="nav-tab__label">{ws.label}</span>
              {showBadge && <span className="nav-tab__badge">{alertCount}</span>}
            </button>
          );
        })}
      </nav>

      <div className="app-header__actions">
        <div className="app-header__status">
          <span className={`status-dot${isOperational ? "" : " status-dot--error"}`} />
          {isOperational ? "Operational" : "Network unavailable"}
        </div>

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
