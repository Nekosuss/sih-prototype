// Shared risk-level presentation (color/label) so the map, the risk
// breakdown bar, and the KPI panel all agree on what "HIGH" looks like.
// Thresholds themselves are NOT duplicated here — the backend
// (app/config.py RISK_LEVEL_THRESHOLDS) is the only source of truth for
// where a score becomes "high" vs "moderate"; this only maps the
// risk_level STRING the backend already returns to a display color.

const RISK_LEVEL_COLORS = {
  low: "#2e7d32",
  moderate: "#c67c00",
  high: "#c0392b",
  critical: "#7b1f1f",
};

export function riskLevelColor(level) {
  return RISK_LEVEL_COLORS[level] || "#6b7280";
}

export function riskLevelLabel(level) {
  return level ? level.toUpperCase() : "UNKNOWN";
}

export const OUTCOME_LABELS = {
  fastest_route_is_safe: "Fastest route is currently acceptable",
  safer_route_selected: "Safer alternative selected",
  no_safe_route_available: "No safe route available",
};

export const OUTCOME_ICON = {
  fastest_route_is_safe: "✓", // check mark
  safer_route_selected: "⚠", // warning triangle
  no_safe_route_available: "\u{1F6D1}", // stop sign
};

// Part 8/12: RouteDecision.outcome (continue/reroute/suspend) -- a DIFFERENT
// enum from RouteSafetyOutcome above (see backend/app/models/route.py),
// produced by the hazard/field-report reroute pipeline.
export const DECISION_LABELS = {
  continue: "Route remains viable",
  reroute: "Reroute required",
  suspend: "Dispatch suspended",
};

export const DECISION_ICON = {
  continue: "✓",
  reroute: "↻", // reroute arrow
  suspend: "!",
};

export const DECISION_ALERT_STYLE = {
  continue: "ok",
  reroute: "warn",
  suspend: "danger",
};

// Part 13: a SINGLE dominant PROCEED/REROUTE/SUSPEND read, synthesized on
// the frontend from whichever real backend result is currently available --
// no new backend concept, no new computation. This is exactly the mapping
// app/models/route.py::RouteSafetyOutcome's docstring anticipated ("that
// mapping is deliberately left to that later part").
//
// hazardDecision (a live RouteDecision from the Part 8/12 hazard pipeline --
// HazardControl.jsx or FieldReportPanel.jsx) always takes priority over
// riskAwareResult (the static Part 6 fastest-vs-safe planning comparison),
// because it reflects CURRENT hazard state; riskAwareResult is the
// baseline read before any hazard/field report exists.
export function deriveDecision({ riskAwareResult, hazardDecision } = {}) {
  if (hazardDecision) {
    const { outcome, reason } = hazardDecision;
    if (outcome === "suspend") {
      return {
        level: "critical",
        title: "Suspend",
        detail: reason || "No safe route is currently available.",
        icon: "!",
      };
    }
    if (outcome === "reroute") {
      return {
        level: "caution",
        title: "Reroute",
        detail: reason || "Current route is unsafe or blocked. A safer alternative is available.",
        icon: "↻",
      };
    }
    return {
      level: "safe",
      title: "Proceed",
      detail: reason || "Route is currently safe to proceed.",
      icon: "✓",
    };
  }

  if (riskAwareResult) {
    const { outcome } = riskAwareResult;
    if (outcome === "no_safe_route_available") {
      return {
        level: "critical",
        title: "Suspend",
        detail: "No route avoids every hard-unsafe segment for this origin/destination.",
        icon: "!",
      };
    }
    if (outcome === "safer_route_selected") {
      return {
        level: "safe",
        title: "Proceed",
        detail: "A safer alternative to the fastest route was selected.",
        icon: "✓",
      };
    }
    return {
      level: "safe",
      title: "Proceed",
      detail: "Route is currently safe to proceed.",
      icon: "✓",
    };
  }

  return null;
}

export const HAZARD_TYPE_LABEL = {
  heavy_rain: "SIMULATED HEAVY RAIN",
  landslide: "SIMULATED LANDSLIDE",
  road_blockage: "SIMULATED ROAD BLOCKAGE",
};

// Part 9: Vehicle.status (deterministic SIMULATED movement, not live GPS).
export const VEHICLE_STATUS_LABEL = {
  idle: "IDLE",
  en_route: "EN ROUTE",
  rerouting: "REROUTING",
  arrived: "ARRIVED",
  suspended: "SUSPENDED",
};

export const VEHICLE_STATUS_COLOR = {
  idle: "#6b7280",
  en_route: "var(--risk-low)",
  rerouting: "var(--risk-moderate)",
  arrived: "var(--color-accent)",
  suspended: "var(--risk-high)",
};

// Part 12: FieldReport.incident_type display labels -- a real-world field
// reporting vocabulary (broader than Part 8's HAZARD_TYPE_LABEL above,
// which only ever describes a demo simulation). Never prefixed "SIMULATED"
// -- these are real (prototype) user-submitted observations, see
// backend/app/models/field_report.py.
export const FIELD_INCIDENT_TYPE_LABEL = {
  landslide: "Landslide",
  road_blockage: "Road Blockage",
  flooding: "Flooding",
  accident: "Accident",
  fallen_tree: "Fallen Tree",
  damaged_road: "Damaged Road",
  other: "Other",
};
