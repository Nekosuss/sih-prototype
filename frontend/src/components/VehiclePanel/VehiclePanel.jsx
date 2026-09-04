import { useEffect, useRef, useState } from "react";
import { createVehicle, getVehicle, pauseVehicle, resetVehicle, startVehicle } from "../../api/client.js";
import { VEHICLE_STATUS_COLOR, VEHICLE_STATUS_LABEL } from "../../utils/risk.js";

const POLL_INTERVAL_MS = 1000;

function formatDuration(minutes) {
  if (minutes == null) return "--";
  const total = Math.round(minutes);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// Part 9: DETERMINISTIC SIMULATED vehicle dispatch/control -- NOT live GPS.
// Dispatches along the currently displayed route's real origin/destination
// (never a hard-coded location) and polls the real backend every ~1s,
// which is the entire "simulation tick" mechanism (see
// backend/app/simulation/vehicle_simulator.py) -- no computation happens
// in this component.
export default function VehiclePanel({ origin, destination, onVehicleUpdate }) {
  const [vehicle, setVehicle] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  function updateVehicle(next) {
    setVehicle(next);
    onVehicleUpdate?.(next);
  }

  useEffect(() => {
    return () => {
      clearInterval(pollRef.current);
      onVehicleUpdate?.(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startPolling(vehicleId) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const updated = await getVehicle(vehicleId);
        updateVehicle(updated);
        if (updated.status === "arrived") clearInterval(pollRef.current);
      } catch (e) {
        clearInterval(pollRef.current);
        setError(e.message);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleDispatch() {
    setBusy(true);
    setError(null);
    try {
      const created = await createVehicle("Demo Vehicle", origin, destination);
      updateVehicle(created);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    setBusy(true);
    try {
      const started = await startVehicle(vehicle.id);
      updateVehicle(started);
      startPolling(vehicle.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handlePause() {
    setBusy(true);
    try {
      updateVehicle(await pauseVehicle(vehicle.id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReset() {
    setBusy(true);
    try {
      clearInterval(pollRef.current);
      updateVehicle(await resetVehicle(vehicle.id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel__title">Vehicle</div>
      <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none", marginBottom: "0.6rem" }}>
        Deterministic simulated movement along the route below — not live GPS.
      </div>

      {!vehicle && (
        <button type="button" className="btn-primary" onClick={handleDispatch} disabled={busy}>
          {busy ? "Dispatching…" : "Dispatch on this route"}
        </button>
      )}

      {vehicle && (
        <>
          <div className="stat-grid" style={{ marginBottom: "0.6rem" }}>
            <div className="stat-tile">
              <span className="stat-tile__label">Status</span>
              <span className="stat-tile__value stat-tile__value--risk">
                <span className="risk-pill" style={{ background: VEHICLE_STATUS_COLOR[vehicle.status] }}>
                  {VEHICLE_STATUS_LABEL[vehicle.status]}
                </span>
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">Progress</span>
              <span className="stat-tile__value">{Math.round(vehicle.progress * 100)}%</span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">Distance Remaining</span>
              <span className="stat-tile__value">{vehicle.distance_remaining_km.toFixed(1)} km</span>
            </div>
            <div className="stat-tile">
              <span className="stat-tile__label">ETA</span>
              <span className="stat-tile__value">{formatDuration(vehicle.eta_minutes)}</span>
            </div>
            {vehicle.route_risk && (
              <div className="stat-tile">
                <span className="stat-tile__label">Route Risk</span>
                <span className="stat-tile__value">{vehicle.route_risk.aggregate_risk_score.toFixed(2)}</span>
              </div>
            )}
          </div>

          {vehicle.last_decision_reason && (vehicle.status === "rerouting" || vehicle.status === "suspended") && (
            <div className={`alert alert--${vehicle.status === "suspended" ? "danger" : "warn"}`} style={{ marginBottom: "0.6rem" }}>
              <span className="alert__icon">{vehicle.status === "suspended" ? "\u{1F6D1}" : "\u{1F504}"}</span>
              <span>{vehicle.last_decision_reason}</span>
            </div>
          )}

          <div style={{ display: "flex", gap: "0.4rem" }}>
            {(vehicle.status === "idle" || vehicle.paused) && (
              <button type="button" className="btn-primary" onClick={handleStart} disabled={busy} style={{ flex: 1 }}>
                {vehicle.paused ? "Resume" : "Start"}
              </button>
            )}
            {!vehicle.paused && (vehicle.status === "en_route" || vehicle.status === "rerouting") && (
              <button type="button" className="btn-primary" onClick={handlePause} disabled={busy} style={{ flex: 1 }}>
                Pause
              </button>
            )}
            <button type="button" className="btn-primary" onClick={handleReset} disabled={busy} style={{ flex: 1 }}>
              Reset
            </button>
          </div>
        </>
      )}

      {error && <div className="form-error">{error}</div>}
    </div>
  );
}
