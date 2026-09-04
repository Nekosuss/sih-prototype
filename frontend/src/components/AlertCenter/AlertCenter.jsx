import { useEffect, useState } from "react";
import {
  clearHazard,
  evaluateDisruption,
  listFieldReports,
  listHazards,
  resolveFieldReport,
} from "../../api/client.js";
import { FIELD_INCIDENT_TYPE_LABEL, HAZARD_TYPE_LABEL } from "../../utils/risk.js";

const POLL_INTERVAL_MS = 5000; // deliberately slower than the ~1s vehicle
// poll (Step 23) -- hazards/field reports change far less often than a
// moving vehicle's position, so polling this fast would be wasted traffic.

// Part 13: ONE consolidated operational alert list, replacing scattered
// per-panel hazard/field-report displays. Both sources feed the SAME
// underlying HazardEvent/StateStore (see backend/app/core/hazard_state.py,
// core/field_report_service.py) -- this component just reads the two list
// endpoints that already exist (GET /hazards, GET /field-reports) and
// renders them as one severity-ordered feed. `routeSegmentIds` (the
// CURRENTLY DISPLAYED route's own segments, if any) is used only to label
// each row "Reroute required" vs "Monitoring" -- a real, already-known
// fact (is this segment on my route?), never a new risk computation.
export default function AlertCenter({ routeSegmentIds, origin, destination, currentRouteId, onDecision, onChange }) {
  const [hazards, setHazards] = useState([]);
  const [reports, setReports] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const [h, r] = await Promise.all([listHazards(true), listFieldReports(true)]);
        if (cancelled) return;
        setHazards(h);
        setReports(r);
        onChange?.({ hazards: h, reports: r });
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    }
    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const routeSet = new Set(routeSegmentIds || []);

  // A field report's own HazardEvent (see hazard_event_id,
  // core/field_report_service.py) also appears in GET /hazards -- exclude
  // it here so the same real-world incident isn't shown twice AND isn't
  // mislabeled: HAZARD_TYPE_LABEL below is Part 8's "SIMULATED ..." demo
  // vocabulary, which must never describe a real field report (Part 12
  // Data Integrity rule 2). The field-report row (below) already shows it
  // honestly, with the correct source.
  const fieldReportHazardIds = new Set(reports.map((r) => r.hazard_event_id).filter(Boolean));

  const hazardRows = hazards
    .filter((h) => !fieldReportHazardIds.has(h.id))
    .map((h) => ({
      key: `hazard-${h.id}`,
      kind: "hazard",
      id: h.id,
      title: HAZARD_TYPE_LABEL[h.type] || h.type,
      severity: h.severity,
      meta: `${h.affected_segment_ids.length} segment(s) affected`,
      onRoute: h.affected_segment_ids.some((sid) => routeSet.has(sid)),
      createdAt: h.created_at,
      resolve: async () => {
        await clearHazard(h.id);
      },
    }));

  const reportRows = reports.map((r) => ({
    key: `report-${r.id}`,
    kind: "field_report",
    id: r.id,
    title: `Field report — ${FIELD_INCIDENT_TYPE_LABEL[r.incident_type] || r.incident_type}`,
    severity: r.severity,
    meta: r.segment_name || r.segment_id,
    onRoute: routeSet.has(r.segment_id),
    createdAt: r.created_at,
    resolve: async () => {
      await resolveFieldReport(r.id);
    },
  }));

  const rows = [...hazardRows, ...reportRows].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));

  async function handleResolve(row) {
    setBusyId(row.id);
    setError(null);
    try {
      await row.resolve();
      const [h, r] = await Promise.all([listHazards(true), listFieldReports(true)]);
      setHazards(h);
      setReports(r);
      onChange?.({ hazards: h, reports: r });

      // Resolving here bypasses HazardControl/FieldReportPanel's own
      // clear/resolve flow, so refresh the shared route decision ourselves
      // (same endpoint those panels already use) -- otherwise the decision
      // banner and map would keep showing a reroute/suspend state the
      // backend no longer holds.
      if (origin && destination) {
        const decision = await evaluateDisruption(origin, destination, currentRouteId);
        onDecision?.(decision);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="panel">
      <div className="panel__title">Alerts{rows.length > 0 ? ` (${rows.length})` : ""}</div>

      {error && <div className="form-error">{error}</div>}

      {rows.length === 0 ? (
        <div className="methodology-note" style={{ marginTop: 0, paddingTop: 0, borderTop: "none" }}>
          No active hazards or field reports.
        </div>
      ) : (
        <div className="alert-center-list">
          {rows.map((row) => {
            const rowClass = row.severity === "blocking" ? "alert-row--blocking" : row.onRoute ? "alert-row--caution" : "";
            return (
              <div className={`alert-row ${rowClass}`} key={row.key}>
                <span className="alert-row__marker" />
                <div className="alert-row__body">
                  <div className="alert-row__title">{row.title}</div>
                  <div className="alert-row__meta">
                    {row.meta} &middot; {row.severity.toUpperCase()}
                  </div>
                  <div
                    className={`alert-row__impact alert-row__impact--${row.onRoute ? "reroute" : "monitoring"}`}
                  >
                    {row.onRoute ? "Affects current route" : "Monitoring — not on current route"}
                  </div>
                </div>
                <button
                  type="button"
                  className="alert-row__action"
                  onClick={() => handleResolve(row)}
                  disabled={busyId === row.id}
                >
                  {busyId === row.id ? "…" : "Resolve"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
