import { useMemo, useState, useEffect } from "react";
import Header from "./components/Header/Header.jsx";
import MapView from "./components/MapView/MapView.jsx";
import RoutePlanner from "./components/RoutePlanner/RoutePlanner.jsx";
import RouteSummary from "./components/RouteSummary/RouteSummary.jsx";
import RouteComparison from "./components/RouteComparison/RouteComparison.jsx";
import RiskBreakdown from "./components/RiskBreakdown/RiskBreakdown.jsx";
import AlertPanel from "./components/AlertPanel/AlertPanel.jsx";
import AlertCenter from "./components/AlertCenter/AlertCenter.jsx";
import EventTimeline from "./components/EventTimeline/EventTimeline.jsx";
import DataSources from "./components/DataSources/DataSources.jsx";
import HazardControl from "./components/HazardControl/HazardControl.jsx";
import VehiclePanel from "./components/VehiclePanel/VehiclePanel.jsx";
import WeatherControls from "./components/WeatherControls/WeatherControls.jsx";
import SegmentDetailPanel from "./components/SegmentDetailPanel/SegmentDetailPanel.jsx";
import FieldReportPanel from "./components/FieldReportPanel/FieldReportPanel.jsx";
import CorridorOverview from "./components/CorridorOverview/CorridorOverview.jsx";
import { VEHICLE_STATUS_LABEL } from "./utils/risk.js";
import { getNetwork, calculateRoute, calculateRiskAwareRoute, resetSimulation } from "./api/client.js";

const ROUTE_TYPE_LABEL = {
  fastest_route_is_safe: "Risk-aware route (fastest route accepted)",
  safer_route_selected: "Risk-aware route (safer alternative)",
  no_safe_route_available: "Fastest route (no safe alternative found)",
};

const MAX_TIMELINE_EVENTS = 40;

export default function App() {
  const [network, setNetwork] = useState(null);
  const [networkError, setNetworkError] = useState(null);

  // Active Workspace Navigation: "dispatch" | "command" | "field" | "lab"
  const [activeWorkspace, setActiveWorkspace] = useState("dispatch");

  const [mode, setMode] = useState("risk-aware"); // "fastest" | "risk-aware"
  const [cargoType, setCargoType] = useState("medical");
  const [fastestResult, setFastestResult] = useState(null);
  const [riskAwareResult, setRiskAwareResult] = useState(null);
  const [routeError, setRouteError] = useState(null);
  const [routeLoading, setRouteLoading] = useState(false);

  // Dynamic hazard-driven RouteDecision (continue/reroute/suspend)
  const [hazardDecision, setHazardDecision] = useState(null);

  // Active simulated vehicle
  const [vehicle, setVehicle] = useState(null);

  // Clicked road segment for diagnostic inspection
  const [selectedSegment, setSelectedSegment] = useState(null); // { id, name } | null

  // Field reporting coordinate picker
  const [pickingLocation, setPickingLocation] = useState(false);
  const [pickedLocation, setPickedLocation] = useState(null); // { lat, lng } | null

  // Operational alert summary & timeline
  const [alertSummary, setAlertSummary] = useState({ hazards: [], reports: [] });
  const [timelineEvents, setTimelineEvents] = useState([]);
  const [dataSourcesOpen, setDataSourcesOpen] = useState(false);
  const [resetting, setResetting] = useState(false);

  function logEvent(label, detail) {
    setTimelineEvents((prev) =>
      [{ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, time: new Date(), label, detail }, ...prev].slice(
        0,
        MAX_TIMELINE_EVENTS
      )
    );
  }

  function handleDecision(decision) {
    setHazardDecision(decision);
    if (decision) {
      const label =
        decision.outcome === "suspend"
          ? "Dispatch suspended"
          : decision.outcome === "reroute"
          ? "Route rerouted"
          : "Route re-confirmed";
      logEvent(label, decision.reason);
    }
  }

  function handleVehicleUpdate(next) {
    setVehicle((prev) => {
      if (next && (!prev || prev.status !== next.status)) {
        logEvent(`Vehicle ${(VEHICLE_STATUS_LABEL[next.status] || next.status).toLowerCase()}`, next.name);
      }
      return next;
    });
  }

  function handleReportSubmitted(report) {
    logEvent(
      "Field report submitted",
      `${report.incident_type.replace("_", " ")} — ${report.segment_name || report.segment_id}`
    );
  }

  function handleReportResolved(report) {
    logEvent("Field report resolved", report.segment_name || report.segment_id);
  }

  function handleMapClick(lat, lng) {
    if (!pickingLocation) return;
    setPickedLocation({ lat, lng });
    setPickingLocation(false);
  }

  useEffect(() => {
    getNetwork()
      .then(setNetwork)
      .catch((e) => setNetworkError(e.message));
  }, []);

  function handleModeChange(nextMode) {
    setMode(nextMode);
    setFastestResult(null);
    setRiskAwareResult(null);
    setRouteError(null);
    setHazardDecision(null);
    setVehicle(null);
  }

  async function handleCalculateRoute(origin, destination, selectedCargo) {
    setRouteLoading(true);
    setRouteError(null);
    setHazardDecision(null);
    setVehicle(null);
    if (selectedCargo) setCargoType(selectedCargo);

    try {
      if (mode === "fastest") {
        const result = await calculateRoute(origin, destination);
        setFastestResult(result);
        setRiskAwareResult(null);
      } else {
        const result = await calculateRiskAwareRoute(origin, destination);
        setRiskAwareResult(result);
        setFastestResult(null);
      }
      logEvent(
        "Convoy route planned",
        `${origin} → ${destination} (${mode === "fastest" ? "fastest" : "risk-aware"})`
      );
    } catch (e) {
      setFastestResult(null);
      setRiskAwareResult(null);
      setRouteError(e.message);
    } finally {
      setRouteLoading(false);
    }
  }

  async function handleResetDemo() {
    setResetting(true);
    try {
      await resetSimulation();
      setFastestResult(null);
      setRiskAwareResult(null);
      setRouteError(null);
      setHazardDecision(null);
      setVehicle(null);
      setSelectedSegment(null);
      setPickingLocation(false);
      setPickedLocation(null);
      setAlertSummary({ hazards: [], reports: [] });
      setTimelineEvents([]);
      logEvent("Demo reset", "Hazards, field reports, and vehicles cleared to baseline");
    } catch (e) {
      setRouteError(e.message);
    } finally {
      setResetting(false);
    }
  }

  let displayRoute = null;
  let routeTypeLabel = null;
  let riskProfile = null;
  let segmentRisks = null;
  let alternativeRoutesAvailable = null;

  if (mode === "fastest" && fastestResult) {
    displayRoute = fastestResult.route;
    routeTypeLabel = "Fastest route";
    alternativeRoutesAvailable = fastestResult.alternative_routes_available;
  } else if (mode === "risk-aware" && riskAwareResult) {
    displayRoute = riskAwareResult.recommended_route || riskAwareResult.fastest_route;
    routeTypeLabel = ROUTE_TYPE_LABEL[riskAwareResult.outcome];
    riskProfile = riskAwareResult.recommended_route_risk || riskAwareResult.fastest_route_risk;
    segmentRisks = riskAwareResult.recommended_route_segment_risks || riskAwareResult.fastest_route_segment_risks;
  }

  const hasResult = Boolean(displayRoute);
  const activeAlertCount = alertSummary.hazards.length + alertSummary.reports.length;

  const segmentsById = useMemo(() => {
    const map = new Map();
    network?.segments.forEach((s) => map.set(s.id, s));
    return map;
  }, [network]);

  const routeSegments = displayRoute
    ? displayRoute.segment_ids.map((id) => ({ id, name: segmentsById.get(id)?.name || null }))
    : network?.segments.slice(0, 25).map((s) => ({ id: s.id, name: s.name })) || [];

  return (
    <div className="app-shell">
      <Header
        activeWorkspace={activeWorkspace}
        onWorkspaceChange={setActiveWorkspace}
        status={networkError ? "error" : network ? "operational" : "loading"}
        alertCount={activeAlertCount}
        onOpenDataSources={() => setDataSourcesOpen(true)}
        onResetDemo={handleResetDemo}
        resetting={resetting}
      />

      <div className="app-main">
        {/* ==================================================================== */}
        {/* LEFT RAIL: CONTEXTUAL TO ACTIVE WORKSPACE                             */}
        {/* ==================================================================== */}
        <aside className="app-rail app-rail--left">
          {networkError && (
            <div className="form-error">
              Failed to load network: {networkError}. Is the backend running on http://localhost:8000?
            </div>
          )}

          {!networkError && !network && <div className="empty-state">Loading road network…</div>}

          {network && (
            <>
              {/* WORKSPACE 1: FLEET DISPATCH */}
              {activeWorkspace === "dispatch" && (
                <>
                  <div className="workspace-banner">
                    <div className="workspace-banner__title">🚚 Fleet &amp; Convoy Dispatch</div>
                    <div className="workspace-banner__desc">
                      Plan risk-weighted transport routes for essential commodities and dispatch monitored vehicles.
                    </div>
                  </div>
                  <RoutePlanner
                    nodes={network.nodes.filter((n) => n.name)}
                    mode={mode}
                    onModeChange={handleModeChange}
                    loading={routeLoading}
                    error={routeError}
                    onCalculate={handleCalculateRoute}
                  />
                </>
              )}

              {/* WORKSPACE 2: COMMAND CENTER */}
              {activeWorkspace === "command" && (
                <>
                  <div className="workspace-banner">
                    <div className="workspace-banner__title">🛡️ Regional Command Center</div>
                    <div className="workspace-banner__desc">
                      State &amp; District Disaster Management oversight: monitor regional bottlenecks and isolated sectors.
                    </div>
                  </div>
                  <CorridorOverview
                    activeAlertCount={activeAlertCount}
                    highRainfallCount={0}
                  />
                  <WeatherControls />
                </>
              )}

              {/* WORKSPACE 3: FIELD REPORTING */}
              {activeWorkspace === "field" && (
                <>
                  <div className="workspace-banner">
                    <div className="workspace-banner__title">📍 Ground Field Reporting</div>
                    <div className="workspace-banner__desc">
                      Log road breaches, rockfalls, and bridge damage directly to the regional intelligence grid.
                    </div>
                  </div>
                  <FieldReportPanel
                    key={`field-report-${displayRoute?.route_id || "standalone"}`}
                    origin={displayRoute?.origin}
                    destination={displayRoute?.destination}
                    currentRouteId={displayRoute?.route_id}
                    pickedLocation={pickedLocation}
                    onStartPicking={() => setPickingLocation(true)}
                    onDecision={handleDecision}
                    onReportSubmitted={handleReportSubmitted}
                    onReportResolved={handleReportResolved}
                  />
                </>
              )}

              {/* WORKSPACE 4: SIMULATION LAB */}
              {activeWorkspace === "lab" && (
                <>
                  <div className="workspace-banner">
                    <div className="workspace-banner__title">🧪 Simulation &amp; Stress Lab</div>
                    <div className="workspace-banner__desc">
                      Simulate hypothetical landslides, severe precipitation, and evaluate dynamic rerouting decisions.
                    </div>
                  </div>
                  <HazardControl
                    key={`hazard-ctrl-${displayRoute?.route_id || "corridor"}`}
                    routeSegments={routeSegments}
                    origin={displayRoute?.origin || "Guwahati"}
                    destination={displayRoute?.destination || "Tawang"}
                    currentRouteId={displayRoute?.route_id}
                    onDecision={handleDecision}
                  />
                  <WeatherControls />
                </>
              )}
            </>
          )}
        </aside>

        {/* ==================================================================== */}
        {/* CENTER: SHARED INTERACTIVE MAP                                       */}
        {/* ==================================================================== */}
        <div className="app-map">
          {network && (
            <MapView
              network={network}
              mode={mode}
              fastestOnlyRoute={fastestResult?.route || null}
              riskAwareResult={riskAwareResult}
              hazardDecision={hazardDecision}
              vehicle={vehicle}
              fieldReports={alertSummary.reports}
              pickingLocation={pickingLocation}
              onMapClick={handleMapClick}
              onSegmentClick={(id, name) => setSelectedSegment({ id, name })}
            />
          )}
        </div>

        {/* ==================================================================== */}
        {/* RIGHT RAIL: CONTEXTUAL TO ACTIVE WORKSPACE                            */}
        {/* ==================================================================== */}
        <aside className="app-rail app-rail--right">
          {network && (
            <>
              {/* Selected Segment Inspection (Always accessible across any workspace) */}
              {selectedSegment && (
                <SegmentDetailPanel
                  segmentId={selectedSegment.id}
                  segmentName={selectedSegment.name}
                  onClose={() => setSelectedSegment(null)}
                />
              )}

              {/* WORKSPACE 1: FLEET DISPATCH */}
              {activeWorkspace === "dispatch" && (
                <>
                  {hasResult && (
                    <AlertPanel
                      riskAwareResult={mode === "risk-aware" ? riskAwareResult : null}
                      hazardDecision={hazardDecision}
                    />
                  )}

                  {!hasResult && !routeLoading && (
                    <div className="empty-state" style={{ padding: "1.4rem 0.6rem" }}>
                      Select an origin, destination, and cargo priority on the left to compute the safest convoy route.
                    </div>
                  )}

                  {hasResult && (
                    <>
                      <RouteSummary
                        route={displayRoute}
                        routeTypeLabel={routeTypeLabel}
                        riskProfile={riskProfile}
                        segmentRisks={segmentRisks}
                        alternativeRoutesAvailable={alternativeRoutesAvailable}
                      />
                      {mode === "risk-aware" && riskAwareResult && <RouteComparison result={riskAwareResult} />}
                      {mode === "risk-aware" && riskProfile && (
                        <RiskBreakdown riskProfile={riskProfile} segmentRisks={segmentRisks} />
                      )}
                      <VehiclePanel
                        key={`vehicle-${displayRoute.route_id}`}
                        origin={displayRoute.origin}
                        destination={displayRoute.destination}
                        onVehicleUpdate={handleVehicleUpdate}
                      />
                    </>
                  )}
                </>
              )}

              {/* WORKSPACE 2: COMMAND CENTER */}
              {activeWorkspace === "command" && (
                <>
                  <AlertCenter
                    routeSegmentIds={displayRoute?.segment_ids}
                    origin={displayRoute?.origin}
                    destination={displayRoute?.destination}
                    currentRouteId={displayRoute?.route_id}
                    onDecision={handleDecision}
                    onChange={setAlertSummary}
                  />
                  {!selectedSegment && (
                    <div className="empty-state" style={{ padding: "1.2rem 0.6rem" }}>
                      Click any road segment on the central map to view real SRTM slope, elevation, GSI historical
                      landslide frequency, and IMD rainfall.
                    </div>
                  )}
                </>
              )}

              {/* WORKSPACE 3: FIELD REPORTING */}
              {activeWorkspace === "field" && (
                <>
                  <AlertCenter
                    routeSegmentIds={displayRoute?.segment_ids}
                    origin={displayRoute?.origin}
                    destination={displayRoute?.destination}
                    currentRouteId={displayRoute?.route_id}
                    onDecision={handleDecision}
                    onChange={setAlertSummary}
                  />
                </>
              )}

              {/* WORKSPACE 4: SIMULATION LAB */}
              {activeWorkspace === "lab" && (
                <>
                  <AlertCenter
                    routeSegmentIds={displayRoute?.segment_ids}
                    origin={displayRoute?.origin}
                    destination={displayRoute?.destination}
                    currentRouteId={displayRoute?.route_id}
                    onDecision={handleDecision}
                    onChange={setAlertSummary}
                  />
                  <div className="panel">
                    <div className="panel__title">Evaluation Sandbox Controls</div>
                    <div className="methodology-note" style={{ marginTop: 0, borderTop: "none" }}>
                      Use the left panel to inject dynamic road obstacles. Watch the system compute
                      CONTINUE / REROUTE / SUSPEND decisions based on graph connectivity.
                    </div>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleResetDemo}
                      disabled={resetting}
                      style={{ marginTop: "0.5rem" }}
                    >
                      {resetting ? "Resetting…" : "Reset Demo Baseline"}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </aside>
      </div>

      <EventTimeline events={timelineEvents} />
      {dataSourcesOpen && <DataSources onClose={() => setDataSourcesOpen(false)} />}
    </div>
  );
}
