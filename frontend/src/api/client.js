const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function postJson(path, payload) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function getNetwork() {
  return getJson("/network");
}

export function getSegment(segmentId) {
  return getJson(`/segments/${segmentId}`);
}

export function getSegmentRisk(segmentId) {
  return getJson(`/segments/${segmentId}/risk`);
}

// Part 5/8: this segment's explainable prototype risk, automatically
// reflecting any currently ACTIVE simulated hazard on it (see
// backend/app/core/hazard_state.py) -- no factors need to be passed here,
// the backend resolves current hazard context itself.
export function getSegmentRiskAware(segmentId) {
  return getJson(`/segments/${segmentId}/risk-aware`);
}

// Baseline (Part 3): fastest route by travel time only. Unchanged by Part 6.
export function calculateRoute(origin, destination) {
  return postJson("/routes/calculate", { origin, destination });
}

// Risk-aware (Part 6): computes + compares the fastest route against the
// risk-aware route in one call. weatherFactor/incidentFactor are optional
// [0,1] externally-supplied current-context inputs (see
// backend/app/core/routing_engine.py) — omit both for "no context supplied".
export function calculateRiskAwareRoute(origin, destination, { weatherFactor, incidentFactor } = {}) {
  const payload = { origin, destination };
  if (weatherFactor != null) payload.weather_factor = weatherFactor;
  if (incidentFactor != null) payload.incident_factor = incidentFactor;
  return postJson("/routes/calculate-risk-aware", payload);
}

// --- Part 8: DEMO SIMULATION -- deterministic simulated hazard events ---
// These are NOT a live weather feed or field-report system. See
// backend/app/models/hazard.py.

export function simulateHazard(type, severity, affectedSegmentIds) {
  return postJson("/hazards/simulate", { type, severity, affected_segment_ids: affectedSegmentIds });
}

export function listHazards(activeOnly = true) {
  return getJson(`/hazards?active_only=${activeOnly}`);
}

export function clearHazard(hazardId) {
  return postJson(`/hazards/${hazardId}/clear`, {});
}

export function resetHazards() {
  return postJson("/hazards/reset", {});
}

// Part 8: CONTINUE / REROUTE / SUSPEND for origin/destination against every
// currently active simulated hazard. previousRouteId is optional -- omit
// for a first-time evaluation with nothing to be "sticky" about.
export function evaluateDisruption(origin, destination, previousRouteId) {
  const payload = { origin, destination };
  if (previousRouteId) payload.previous_route_id = previousRouteId;
  return postJson("/routes/evaluate-disruption", payload);
}

// --- Part 9: DETERMINISTIC SIMULATED vehicle movement ---
// NOT live GPS or real-time tracking -- see backend/app/models/vehicle.py.

export function createVehicle(name, origin, destination, mode = "risk-aware") {
  return postJson("/vehicles", { name, origin, destination, mode });
}

export function listVehicles() {
  return getJson("/vehicles");
}

export function getVehicle(vehicleId) {
  return getJson(`/vehicles/${vehicleId}`);
}

export function startVehicle(vehicleId) {
  return postJson(`/vehicles/${vehicleId}/start`, {});
}

export function pauseVehicle(vehicleId) {
  return postJson(`/vehicles/${vehicleId}/pause`, {});
}

export function resetVehicle(vehicleId) {
  return postJson(`/vehicles/${vehicleId}/reset`, {});
}

// --- Part 10: REAL IMD gridded rainfall (0.25 x 0.25 deg) ---
// An ADDITIONAL, real-data input path alongside Part 8's simulated hazards
// -- see backend/app/data/rainfall_loader.py. `date` is an optional ISO
// YYYY-MM-DD string; omitting it uses the backend's fixed demo observation
// date (never the machine's current date -- this is historical data, not a
// live feed or forecast).

export function getCorridorWeather(date) {
  return getJson(`/weather/corridor${date ? `?date=${date}` : ""}`);
}

export function getSegmentWeather(segmentId, date) {
  return getJson(`/weather/segments/${segmentId}${date ? `?date=${date}` : ""}`);
}

// --- Part 11: landslide/flood HAZARD-ZONATION layers ---
// A DIFFERENT concept from Part 8's simulated hazard EVENTS above: this is
// the real (or, currently, honestly-unavailable -- see
// backend/app/data/hazard_layer_loader.py) spatial susceptibility layer.

export function getHazardLayers() {
  return getJson("/hazards/layers");
}

export function getSegmentHazardLayers(segmentId) {
  return getJson(`/hazards/segments/${segmentId}`);
}

// --- Part 12: field reporting / incident intelligence ---
// Real (prototype) field-worker-submitted incident reports -- GPS-matched to
// the nearest real OSM road segment and fed into the SAME hazard/risk/
// reroute pipeline Part 8's simulated hazards use (see
// backend/app/core/field_report_service.py). NEVER labeled "SIMULATED" --
// source is always "field_report". origin/destination/previousRouteId are
// optional: when supplied, the response's route_decision reports this
// report's CONTINUE/REROUTE/SUSPEND impact on that specific route in the
// same call, reusing the exact same decision the /routes/evaluate-disruption
// endpoint would return.

export function createFieldReport(report, routeContext = {}) {
  const payload = {
    incident_type: report.incidentType,
    severity: report.severity,
    latitude: report.latitude,
    longitude: report.longitude,
    description: report.description,
  };
  if (report.reporterName) payload.reporter_name = report.reporterName;
  if (routeContext.origin) payload.origin = routeContext.origin;
  if (routeContext.destination) payload.destination = routeContext.destination;
  if (routeContext.previousRouteId) payload.previous_route_id = routeContext.previousRouteId;
  return postJson("/field-reports", payload);
}

export function listFieldReports(activeOnly = true) {
  return getJson(`/field-reports?active_only=${activeOnly}`);
}

export function getFieldReport(reportId) {
  return getJson(`/field-reports/${reportId}`);
}

// --- Part 13: demo reset ---
// Restores hazards/field reports/vehicles to a known baseline. Does not
// touch any static source dataset (see backend/app/api/routes_simulation.py).

export function resetSimulation() {
  return postJson("/simulation/reset", {});
}

export function resolveFieldReport(reportId, routeContext = {}) {
  const payload = {};
  if (routeContext.origin) payload.origin = routeContext.origin;
  if (routeContext.destination) payload.destination = routeContext.destination;
  if (routeContext.previousRouteId) payload.previous_route_id = routeContext.previousRouteId;
  return postJson(`/field-reports/${reportId}/resolve`, payload);
}
