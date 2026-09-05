// Regional Command Center overview: Guwahati -> Tawang corridor status,
// key monitoring nodes, and live connectivity health across districts.
const STATIONS = [
  { name: "Guwahati", district: "Kamrup Metro (Assam)", elevation: "~60m", role: "Primary Logistics Base" },
  { name: "Tezpur", district: "Sonitpur (Assam)", elevation: "~81m", role: "Transit Hub" },
  { name: "Bhalukpong", district: "West Kameng Entry", elevation: "~176m", role: "Gateway Checkpoint" },
  { name: "Bomdila", district: "West Kameng HQ", elevation: "~2,428m", role: "Mountain Transit Center" },
  { name: "Dirang", district: "West Kameng", elevation: "~1,632m", role: "Valley Depot" },
  { name: "Sela Pass", district: "West Kameng / Tawang", elevation: "~3,733m", role: "High-Altitude Chokepoint" },
  { name: "Tawang", district: "Tawang District HQ", elevation: "~2,904m", role: "Terminal Border Station" },
];

export default function CorridorOverview({ activeAlertCount, highRainfallCount, onSelectStation }) {
  const isSevered = activeAlertCount > 0;

  return (
    <div className="panel">
      <div className="panel__title">Corridor Health &amp; Accessibility</div>

      <div
        className="callout"
        style={{
          borderLeft: `4px solid ${isSevered ? "var(--status-caution)" : "var(--status-safe)"}`,
          background: isSevered ? "var(--status-caution-bg)" : "var(--status-safe-bg)",
          marginBottom: "0.85rem",
        }}
      >
        <div style={{ fontWeight: 600, fontSize: "0.8rem", color: "var(--color-ink)" }}>
          {isSevered
            ? `⚠️ ${activeAlertCount} active obstacle(s) reported along corridor`
            : "🟢 Lifeline Arterial Route Open & Accessible"}
        </div>
        <div style={{ fontSize: "0.72rem", color: "var(--color-ink-soft)", marginTop: "0.2rem" }}>
          NH-13 / Trans-Arunachal Highway connecting Assam plains to Western Arunachal frontier.
        </div>
      </div>

      <div className="field-group">
        <div className="field-label">Key Corridor Monitoring Nodes</div>
        <div className="station-checklist">
          {STATIONS.map((s) => (
            <div
              key={s.name}
              className="station-node-row"
              onClick={() => onSelectStation?.(s.name)}
              title={`Click to focus map on ${s.name}`}
            >
              <div className="station-node-row__info">
                <span className="station-node-row__name">{s.name}</span>
                <span className="station-node-row__district">{s.district}</span>
              </div>
              <div className="station-node-row__meta">
                <span className="station-node-row__elevation">{s.elevation}</span>
                <span className="station-node-row__role">{s.role}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="methodology-note" style={{ marginTop: "0.5rem" }}>
        <strong>Command Guidance:</strong> Click any road segment on the central map to inspect NASA SRTM slope, GSI
        landslides, and IMD rainfall.
      </div>
    </div>
  );
}
