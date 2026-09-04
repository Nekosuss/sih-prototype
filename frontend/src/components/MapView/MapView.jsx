import { useMemo } from "react";
import { MapContainer, TileLayer, Marker, CircleMarker, Popup, Polyline, useMapEvents } from "react-leaflet";
import L from "leaflet";
import icon from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";
import { riskLevelColor, riskLevelLabel, VEHICLE_STATUS_COLOR, VEHICLE_STATUS_LABEL } from "../../utils/risk.js";

// react-leaflet's default marker icon path resolution breaks under Vite's
// bundling; point it at the bundled asset URLs explicitly.
const defaultIcon = L.icon({
  iconUrl: icon,
  shadowUrl: iconShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
});
L.Marker.prototype.options.icon = defaultIcon;

const NEUTRAL_ROAD_COLOR = "#9aa7b0";
const ACCENT_ROUTE_COLOR = "#155e75";

// Weight major roads more heavily than minor connectors so the primary
// corridor stays visually dominant even with many branch segments loaded.
function roadWeight(roadType) {
  if (roadType?.startsWith("trunk")) return 5;
  if (roadType?.startsWith("primary")) return 4;
  if (roadType?.startsWith("secondary")) return 3;
  if (roadType?.startsWith("tertiary")) return 2;
  return 1.5;
}

// Part 11: landslide/flood hazard-zonation display, shared by both popups
// below. Reads directly off the already-loaded segment object -- these are
// just RoadSegment fields (see backend/app/models/network.py), so this adds
// zero extra network requests. Honestly shows "not available" rather than
// a fabricated value when no official APSAC layer covers this segment
// (currently true for every segment -- see backend/app/data/README.md Part 11).
function HazardZonationLines({ seg }) {
  return (
    <>
      <br />
      Landslide hazard: {seg.landslide_hazard_class || "not available (no official layer)"}
      <br />
      Flood hazard: {seg.flood_hazard_class || "not available (no official layer)"}
    </>
  );
}

function BackgroundSegmentPopup({ seg }) {
  return (
    <Popup>
      <div className="segment-popup">
        <div className="segment-popup__title">{seg.name || seg.id}</div>
        Road class: {seg.road_type}
        <br />
        Distance: {seg.distance_km} km &middot; Est. time: {Math.round(seg.estimated_travel_time_min)} min
        <br />
        Elevation: {seg.elevation_m != null ? `${Math.round(seg.elevation_m)} m` : "n/a"}
        {seg.slope_deg != null && (
          <>
            <br />
            Slope: {seg.slope_deg.toFixed(1)}&deg; (real SRTM DEM)
          </>
        )}
        {seg.historical_landslide_count > 0 ? (
          <>
            <br />
            Historical landslides: {seg.historical_landslide_count}
            {seg.nearest_landslide_distance_m != null &&
              ` (nearest ${Math.round(seg.nearest_landslide_distance_m)}m)`}
          </>
        ) : (
          <>
            <br />
            Historical landslides: 0 (no matched GSI record — not proof of safety)
          </>
        )}
        <HazardZonationLines seg={seg} />
        <div className="segment-popup__note">
          Terrain is real (SRTM DEM); this view does not show the prototype risk
          engine's score — click the segment for full detail, or calculate a
          Risk-Aware route.
        </div>
      </div>
    </Popup>
  );
}

function RouteSegmentPopup({ seg, riskResult }) {
  return (
    <Popup>
      <div className="segment-popup">
        <div className="segment-popup__title">{seg.name || seg.id}</div>
        {riskResult && (
          <div className="segment-popup__risk-line">
            <span className="risk-pill" style={{ background: riskLevelColor(riskResult.risk_level) }}>
              {riskLevelLabel(riskResult.risk_level)}
            </span>
            <span>
              Risk Score: <strong>{riskResult.risk_score.toFixed(2)}</strong>
            </span>
          </div>
        )}
        {seg.slope_deg != null && <>Slope: {seg.slope_deg.toFixed(1)}&deg;<br /></>}
        Historical Landslides: {seg.historical_landslide_count}
        {seg.nearest_landslide_distance_m != null && (
          <>
            <br />
            Nearest Historical Landslide: {Math.round(seg.nearest_landslide_distance_m)}m
          </>
        )}
        <HazardZonationLines seg={seg} />
        {riskResult && riskResult.reasons.length > 0 && (
          <ul>
            {riskResult.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
        {!riskResult && (
          <div className="segment-popup__note">
            Prototype risk score not available for this route (fastest-only mode).
          </div>
        )}
      </div>
    </Popup>
  );
}

// Renders one route as a series of individual per-segment polylines (rather
// than one combined polyline) so each segment can carry its own color
// (real per-segment risk, when available) and its own click popup.
function RouteSegments({ segmentIds, segmentLookup, riskResults, prominent, colorByRisk, onSegmentClick }) {
  const riskById = useMemo(() => {
    const map = new Map();
    (riskResults || []).forEach((r) => map.set(r.segment_id, r));
    return map;
  }, [riskResults]);

  return (
    <>
      {segmentIds.map((id) => {
        const seg = segmentLookup.get(id);
        if (!seg) return null;
        const riskResult = riskById.get(id);
        const color = colorByRisk && riskResult ? riskLevelColor(riskResult.risk_level) : ACCENT_ROUTE_COLOR;

        return (
          <Polyline
            key={id}
            positions={seg.geometry.map((p) => [p.lat, p.lng])}
            pathOptions={
              prominent
                ? { color, weight: 6, opacity: 0.95 }
                : { color: "#9aa7b0", weight: 4, opacity: 0.55, dashArray: "2 8" }
            }
            eventHandlers={onSegmentClick ? { click: () => onSegmentClick(seg.id, seg.name) } : undefined}
          >
            {prominent && <RouteSegmentPopup seg={seg} riskResult={riskResult} />}
          </Polyline>
        );
      })}
    </>
  );
}

// Part 12: active while the field-report form is waiting for "USE MAP
// LOCATION" -- a click anywhere on the map hands the real clicked
// coordinates back to the form (no fabricated/rounded-to-town location).
// Renders nothing itself; only registers the click listener.
function LocationPicker({ active, onPick }) {
  useMapEvents({
    click(e) {
      if (active) onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

export default function MapView({
  network,
  mode,
  fastestOnlyRoute,
  riskAwareResult,
  hazardDecision,
  vehicle,
  fieldReports,
  pickingLocation,
  onMapClick,
  onSegmentClick,
}) {
  const { nodes, segments } = network;
  const bounds = nodes.map((n) => [n.lat, n.lng]);
  const namedNodes = nodes.filter((n) => n.name);

  const segmentLookup = useMemo(() => {
    const map = new Map();
    segments.forEach((s) => map.set(s.id, s));
    return map;
  }, [segments]);

  // Decide what to draw for the active route(s), independent of mode.
  let muted = null; // { segmentIds } drawn faint/dashed underneath, if any
  let prominent = null; // { segmentIds, riskResults, colorByRisk } drawn solid on top
  let originId = null;
  let destinationId = null;
  let hazardHighlightSegmentIds = [];

  if (hazardDecision) {
    // Part 8: a hazard decision TAKES OVER the route display -- it is the
    // most current, relevant state once a simulated hazard is active.
    // Never fabricated: previous_route/recommended_route are real routes
    // already returned by POST /routes/evaluate-disruption.
    const { outcome, previous_route, recommended_route, affected_segment_ids } = hazardDecision;
    const anchor = previous_route || recommended_route;
    if (anchor) {
      originId = anchor.node_ids[0];
      destinationId = anchor.node_ids[anchor.node_ids.length - 1];
    }
    if (outcome === "reroute" && previous_route && recommended_route) {
      muted = { segmentIds: previous_route.segment_ids };
      prominent = { segmentIds: recommended_route.segment_ids, riskResults: null, colorByRisk: false };
    } else if (recommended_route) {
      prominent = { segmentIds: recommended_route.segment_ids, riskResults: null, colorByRisk: false };
    } else if (previous_route) {
      // suspend: no safe route exists -- show the attempted route plainly;
      // the blocked segment(s) are what the hazard highlight layer below is for.
      prominent = { segmentIds: previous_route.segment_ids, riskResults: null, colorByRisk: false };
    }
    hazardHighlightSegmentIds = affected_segment_ids || [];
  } else if (mode === "fastest" && fastestOnlyRoute) {
    prominent = { segmentIds: fastestOnlyRoute.segment_ids, riskResults: null, colorByRisk: false };
    originId = fastestOnlyRoute.node_ids[0];
    destinationId = fastestOnlyRoute.node_ids[fastestOnlyRoute.node_ids.length - 1];
  } else if (mode === "risk-aware" && riskAwareResult) {
    const { outcome, fastest_route, recommended_route, fastest_route_segment_risks, recommended_route_segment_risks } =
      riskAwareResult;
    originId = fastest_route.node_ids[0];
    destinationId = fastest_route.node_ids[fastest_route.node_ids.length - 1];

    if (outcome === "safer_route_selected" && recommended_route) {
      muted = { segmentIds: fastest_route.segment_ids };
      prominent = {
        segmentIds: recommended_route.segment_ids,
        riskResults: recommended_route_segment_risks,
        colorByRisk: true,
      };
    } else {
      // fastest_route_is_safe (recommended === fastest) OR no_safe_route_available
      // (only the fastest route exists) -- either way, one prominent route,
      // colored by its own real per-segment risk.
      prominent = {
        segmentIds: fastest_route.segment_ids,
        riskResults: fastest_route_segment_risks,
        colorByRisk: true,
      };
    }
  }

  return (
    <MapContainer
      bounds={bounds}
      boundsOptions={{ padding: [30, 30] }}
      style={{ height: "100%", width: "100%", cursor: pickingLocation ? "crosshair" : undefined }}
      scrollWheelZoom
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {onMapClick && <LocationPicker active={Boolean(pickingLocation)} onPick={onMapClick} />}

      {/* Background: the real road network, muted -- context only, not a hazard overlay. */}
      {segments.map((seg) => (
        <Polyline
          key={seg.id}
          positions={seg.geometry.map((p) => [p.lat, p.lng])}
          pathOptions={{ color: NEUTRAL_ROAD_COLOR, weight: roadWeight(seg.road_type), opacity: 0.45 }}
          eventHandlers={onSegmentClick ? { click: () => onSegmentClick(seg.id, seg.name) } : undefined}
        >
          <BackgroundSegmentPopup seg={seg} />
        </Polyline>
      ))}

      {muted && (
        <RouteSegments
          segmentIds={muted.segmentIds}
          segmentLookup={segmentLookup}
          riskResults={null}
          prominent={false}
          colorByRisk={false}
          onSegmentClick={onSegmentClick}
        />
      )}
      {prominent && (
        <RouteSegments
          segmentIds={prominent.segmentIds}
          segmentLookup={segmentLookup}
          riskResults={prominent.riskResults}
          prominent
          colorByRisk={prominent.colorByRisk}
          onSegmentClick={onSegmentClick}
        />
      )}

      {/* Part 8: hazard-affected segment(s), highlighted regardless of
          route membership -- real OSM geometry, looked up the same way as
          every other segment; never a fabricated overlay. */}
      {hazardHighlightSegmentIds.map((id) => {
        const seg = segmentLookup.get(id);
        if (!seg) return null;
        return (
          <Polyline
            key={`hazard-${id}`}
            positions={seg.geometry.map((p) => [p.lat, p.lng])}
            pathOptions={{ color: "#c0392b", weight: 9, opacity: 0.85, dashArray: "1 9", lineCap: "round" }}
          />
        );
      })}

      {namedNodes.map((node) => (
        <Marker key={node.id} position={[node.lat, node.lng]}>
          <Popup>
            <strong>{node.name}</strong>
            <br />
            Type: {node.type}
            {node.elevation_m != null && (
              <>
                <br />
                Elevation: {Math.round(node.elevation_m)} m
              </>
            )}
          </Popup>
        </Marker>
      ))}

      {originId &&
        (() => {
          const n = nodes.find((x) => x.id === originId);
          return n ? <CircleMarker center={[n.lat, n.lng]} radius={7} pathOptions={{ color: "#1f7a3f", fillColor: "#1f7a3f", fillOpacity: 1 }} /> : null;
        })()}
      {destinationId &&
        (() => {
          const n = nodes.find((x) => x.id === destinationId);
          return n ? <CircleMarker center={[n.lat, n.lng]} radius={7} pathOptions={{ color: "#a33", fillColor: "#a33", fillOpacity: 1 }} /> : null;
        })()}

      {/* Part 9: DETERMINISTIC SIMULATED vehicle position -- never live GPS.
          Real lat/lng from the backend's interpolation along real route
          geometry (see backend/app/simulation/vehicle_simulator.py). */}
      {vehicle && vehicle.current_lat != null && vehicle.current_lng != null && (
        <CircleMarker
          center={[vehicle.current_lat, vehicle.current_lng]}
          radius={9}
          pathOptions={{
            color: "#fff",
            weight: 2,
            fillColor: VEHICLE_STATUS_COLOR[vehicle.status] || ACCENT_ROUTE_COLOR,
            fillOpacity: 1,
          }}
        >
          <Popup>
            <div className="segment-popup">
              <div className="segment-popup__title">{vehicle.name}</div>
              Status: {VEHICLE_STATUS_LABEL[vehicle.status] || vehicle.status}
              <br />
              Progress: {Math.round(vehicle.progress * 100)}%
              <br />
              Distance remaining: {vehicle.distance_remaining_km.toFixed(1)} km
              <div className="segment-popup__note">Deterministic simulated position -- not live GPS.</div>
            </div>
          </Popup>
        </CircleMarker>
      )}

      {/* Part 12: real (prototype) field-worker incident reports -- a
          distinct visual treatment (solid purple marker, not the vehicle's
          status-colored one) at the REAL reported GPS coordinates, never at
          the matched segment's own geometry. */}
      {(fieldReports || []).map((report) => (
        <CircleMarker
          key={report.id}
          center={[report.latitude, report.longitude]}
          radius={8}
          pathOptions={{ color: "#fff", weight: 2, fillColor: "#6d28d9", fillOpacity: 0.95 }}
        >
          <Popup>
            <div className="segment-popup">
              <div className="segment-popup__title">
                {report.incident_type.replace("_", " ").toUpperCase()} — {report.severity.toUpperCase()}
              </div>
              Segment: {report.segment_name || report.segment_id}
              <br />
              Distance to road: {Math.round(report.distance_to_road_m)} m
              <br />
              {report.description}
              <div className="segment-popup__note">
                Real field_report observation -- not a verified GSI/APSAC/IMD record.
              </div>
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
