import { useEffect, useState } from "react";
import MapView from "./components/MapView/MapView.jsx";
import RoutePlanner from "./components/RoutePlanner/RoutePlanner.jsx";
import { getNetwork, calculateRoute } from "./api/client.js";

export default function App() {
  const [network, setNetwork] = useState(null);
  const [error, setError] = useState(null);

  const [route, setRoute] = useState(null);
  const [alternativeRoutesAvailable, setAlternativeRoutesAvailable] = useState(false);
  const [routeError, setRouteError] = useState(null);
  const [routeLoading, setRouteLoading] = useState(false);

  useEffect(() => {
    getNetwork()
      .then(setNetwork)
      .catch((e) => setError(e.message));
  }, []);

  async function handleCalculateRoute(origin, destination) {
    setRouteLoading(true);
    setRouteError(null);
    try {
      const result = await calculateRoute(origin, destination);
      setRoute(result.route);
      setAlternativeRoutesAvailable(result.alternative_routes_available);
    } catch (e) {
      setRoute(null);
      setRouteError(e.message);
    } finally {
      setRouteLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <header style={{ padding: "0.75rem 1rem", borderBottom: "1px solid #ddd" }}>
        <h1 style={{ margin: 0, fontSize: "1.1rem" }}>
          NER Hazard-Aware Logistics — Guwahati &rarr; Tawang Corridor
        </h1>
        <p style={{ margin: "0.25rem 0 0", fontSize: "0.8rem", color: "#666" }}>
          Real branched OpenStreetMap road network + baseline routing
          (fastest route by travel time — not hazard/risk-aware yet).
          Terrain is estimated and hazard susceptibility is not yet
          assessed; see backend/app/data/README.md.
        </p>
      </header>
      <main style={{ flex: 1, minHeight: 0, position: "relative" }}>
        {error && (
          <p style={{ padding: "1rem", color: "#b00020" }}>
            Failed to load network: {error}. Is the backend running on
            http://localhost:8000?
          </p>
        )}
        {!error && !network && <p style={{ padding: "1rem" }}>Loading network…</p>}
        {network && (
          <>
            <RoutePlanner
              nodes={network.nodes.filter((n) => n.name)}
              route={route}
              alternativeRoutesAvailable={alternativeRoutesAvailable}
              error={routeError}
              loading={routeLoading}
              onCalculate={handleCalculateRoute}
            />
            <MapView network={network} route={route} />
          </>
        )}
      </main>
    </div>
  );
}
