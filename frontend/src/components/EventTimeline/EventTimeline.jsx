import { useState } from "react";

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Part 13: a compact, collapsible strip of what actually happened this
// session -- route calculated, vehicle dispatched/rerouted/arrived, hazard
// triggered/cleared, field report submitted/resolved. Every entry is
// logged by App.jsx at the exact moment a real backend call succeeds (see
// App.jsx::logEvent) -- this component only renders the list, it never
// invents or infers an event on its own.
export default function EventTimeline({ events }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <footer className="app-timeline">
      <div className="app-timeline__header" onClick={() => setCollapsed((c) => !c)}>
        <span className="app-timeline__header-label">Activity ({events.length})</span>
        <span className="app-timeline__toggle">{collapsed ? "Show ▲" : "Hide ▼"}</span>
      </div>
      {!collapsed && (
        <div className="app-timeline__body">
          {events.length === 0 ? (
            <div className="empty-state" style={{ padding: "0.5rem 0" }}>
              No activity yet this session.
            </div>
          ) : (
            events.map((e) => (
              <div className="timeline-item" key={e.id}>
                <div className="timeline-item__time">{formatTime(e.time)}</div>
                <div className="timeline-item__label">{e.label}</div>
                {e.detail && <div className="timeline-item__detail">{e.detail}</div>}
              </div>
            ))
          )}
        </div>
      )}
    </footer>
  );
}
