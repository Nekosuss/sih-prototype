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

  const [mode, setMode] = useState("risk-aware"); // "fastest" | "risk-aware"
  const [fastestResult, setFastestResult] = useState(null); // { route, alternative_routes_available }
  const [riskAwareResult, setRiskAwareResult] = useState(null); // RiskAwareRouteResult
  const [routeError, setRouteError] = useState(null);
  const [routeLoading, setRouteLoading] = useState(false);

  // Part 8/12: the most recent hazard-driven RouteDecision (continue/reroute/
  // suspend) from either the demo hazard controls or a field report -- both
  // funnel into this ONE slot, since they feed the same backend pipeline.
  // TAKES OVER the map's route display and the decision banner while set --
  // cleared whenever a fresh route is calculated.
  const [hazardDecision, setHazardDecision] = useState(null);

  // Part 9: the currently dispatched simulated vehicle (or null), reported
  // up from VehiclePanel so the map can render its live position.
  const [vehicle, setVehicle] = useState(null);

  // Part 11: the currently clicked road segment (or null) -- drives
  // SegmentDetailPanel's combined hazard/rainfall/risk fetch for just that
  // ONE segment, never all ~2,964 at once.
  const [selectedSegment, setSelectedSegment] = useState(null); // { id, name } | null

  // Part 12: field-worker incident reports. Map markers are sourced from
  // AlertCenter's own poll (alertSummary.reports below) rather than a
  // second independent fetch here -- one poll, not two (Step 23).
  // `pickingLocation` + `pickedLocation` implement "USE MAP LOCATION" -- a
  // click on the map hands its REAL coordinates back to the form, never a
  // fabricated/rounded location.
  const [pickingLocation, setPickingLocation] = useState(false);
  const [pickedLocation, setPickedLocation] = useState(null); // { lat, lng } | null

  // Part 13: consolidated operational state -- what the Alert Center last
  // saw (for the header's active-incident count), the session activity
  // timeline, the Data Sources overlay, and Reset Demo's in-flight state.
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

  // Shared by HazardControl, FieldReportPanel, and AlertCenter -- every
  // real source of a RouteDecision funnels through here so the decision
  // banner/map/timeline stay consistent no matter which panel triggered it.
  function handleDecision(decision) {
    setHazardDecision(decision);
    if (decision) {
      const label =
        decision.outcome === "suspend" ? "Dispatch suspended" : decision.outcome === "reroute" ? "Route rerouted" : "Route re-confirmed";
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
    logEvent("Field report submitted", `${report.incident_type.replace("_", " ")} — ${report.segment_name || report.segment_id}`);
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

  async function handleCalculateRoute(origin, destination) {
    setRouteLoading(true);
    setRouteError(null);
    setHazardDecision(null);
    setVehicle(null);
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
      logEvent("Route calculated", `${origin} → ${destination} (${mode === "fastest" ? "fastest" : "risk-aware"})`);
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
      logEvent("Demo reset", "Hazards, field reports, and vehicles cleared");
    } catch (e) {
      setRouteError(e.message);
    } finally {
      setResetting(false);
    }
  }

  // Normalize the two possible result shapes into one view model so the
  // display components stay simple. Everything here is a plain lookup into
  // real backend response fields -- no values are computed/invented here.
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

  // Real segments on the currently displayed route only (never the whole
  // network) -- for the hazard-simulation segment picker.
  const routeSegments = displayRoute
    ? displayRoute.segment_ids.map((id) => ({ id, name: segmentsById.get(id)?.name || null }))
    : [];

  return (
    <div className="app-shell">
      <Header
        status={networkError ? "error" : network ? "operational" : "loading"}
        alertCount={activeAlertCount}
        onOpenDataSources={() => setDataSourcesOpen(true)}
        onResetDemo={handleResetDemo}
        resetting={resetting}
      />
      <div className="app-main">
        <aside className="app-rail app-rail--left">
          {networkError && (
            <div className="form-error">
              Failed to load network: {networkError}. Is the backend running on http://localhost:8000?
            </div>
          )}

          {!networkError && !network && <div className="empty-state">Loading road network…</div>}

          {network && (
            <>
              <RoutePlanner
                nodes={network.nodes.filter((n) => n.name)}
                mode={mode}
                onModeChange={handleModeChange}
                loading={routeLoading}
                error={routeError}
                onCalculate={handleCalculateRoute}
              />

              <WeatherControls />

              {hasResult && (
                <HazardControl
                  key={displayRoute.route_id}
                  routeSegments={routeSegments}
                  origin={displayRoute.origin}
                  destination={displayRoute.destination}
                  currentRouteId={displayRoute.route_id}
                  onDecision={handleDecision}
                />
              )}
            </>
          )}
        </aside>

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

        <aside className="app-rail app-rail--right">
          {network && (
            <>
              {selectedSegment && (
                <SegmentDetailPanel
                  segmentId={selectedSegment.id}
                  segmentName={selectedSegment.name}
                  onClose={() => setSelectedSegment(null)}
                />
              )}

              {hasResult && (
                <AlertPanel riskAwareResult={mode === "risk-aware" ? riskAwareResult : null} hazardDecision={hazardDecision} />
              )}

              <AlertCenter
                routeSegmentIds={displayRoute?.segment_ids}
                origin={displayRoute?.origin}
                destination={displayRoute?.destination}
                currentRouteId={displayRoute?.route_id}
                onDecision={handleDecision}
                onChange={setAlertSummary}
              />

              {!hasResult && !routeLoading && (
                <div className="empty-state" style={{ padding: "1.2rem 0.5rem" }}>
                  Select an origin and destination to begin.
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
                  <FieldReportPanel
                    key={`field-report-${displayRoute.route_id}`}
                    origin={displayRoute.origin}
                    destination={displayRoute.destination}
                    currentRouteId={displayRoute.route_id}
                    pickedLocation={pickedLocation}
                    onStartPicking={() => setPickingLocation(true)}
                    onDecision={handleDecision}
                    onReportSubmitted={handleReportSubmitted}
                    onReportResolved={handleReportResolved}
                  />
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
        </aside>
      </div>

      <EventTimeline events={timelineEvents} />
      {dataSourcesOpen && <DataSources onClose={() => setDataSourcesOpen(false)} />}
    </div>
  );
}
