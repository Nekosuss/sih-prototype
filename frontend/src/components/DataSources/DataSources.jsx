// Part 13 (Step 17): a small, honest provenance reference -- what in this
// application is real data vs. a documented prototype simplification. Pure
// static content; nothing here is fetched or computed. Mirrors
// ARCHITECTURE.md section 8 ("Real vs. Simulated Data") and the per-source
// module docstrings (backend/app/data/README.md,
// app/data/hazard_layer_loader.py) -- this is a UI summary of what those
// already state, not a new claim.
const SOURCES = [
  {
    name: "Road network",
    tag: "real",
    desc: "OpenStreetMap extract for the Guwahati–Tawang corridor (real topology, geometry, and road classification).",
  },
  {
    name: "Terrain / slope",
    tag: "real",
    desc: "NASA SRTM 1-arc-second DEM, sampled along each road segment's real geometry.",
  },
  {
    name: "Historical landslides",
    tag: "real",
    desc: "Geological Survey of India (GSI) landslide inventory, spatially matched to the nearest real road segment within 500m.",
  },
  {
    name: "Rainfall",
    tag: "real",
    desc: "IMD gridded daily rainfall (0.25° resolution), historical observations — not a live feed or forecast.",
  },
  {
    name: "Landslide / flood hazard zonation",
    tag: "unavailable",
    desc: "Official APSAC/SRSAC hazard-zonation layers. Production data is not locally available; access requires an official data request to APSAC/SRSAC. No zonation values are fabricated in their place.",
  },
  {
    name: "Hazard events & field reports",
    tag: "prototype",
    desc: "Demo-triggered hazards and field-worker incident reports are real prototype inputs run through the real risk/routing pipeline — not live GPS, not a live weather feed, and not verified government records.",
  },
];

const TAG_LABEL = { real: "Real data", unavailable: "Unavailable", prototype: "Prototype input" };

export default function DataSources({ onClose }) {
  return (
    <div className="overlay-backdrop" onClick={onClose}>
      <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="overlay-panel__header">
          <span className="overlay-panel__title">Data &amp; Coverage</span>
          <button type="button" className="overlay-panel__close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        {SOURCES.map((s) => (
          <div className="data-source-row" key={s.name}>
            <span className="data-source-row__name">{s.name}</span>
            <span className={`data-source-row__tag data-source-row__tag--${s.tag === "real" ? "real" : "unavailable"}`}>
              {TAG_LABEL[s.tag]}
            </span>
            <div className="data-source-row__desc">{s.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
