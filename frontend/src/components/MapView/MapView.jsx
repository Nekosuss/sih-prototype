import { MapContainer, TileLayer, Marker, Popup, Polyline } from "react-leaflet";
import L from "leaflet";
import icon from "leaflet/dist/images/marker-icon.png";
import iconShadow from "leaflet/dist/images/marker-shadow.png";

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

function riskColor(risk) {
  if (risk >= 0.5) return "#c0392b"; // high
  if (risk >= 0.25) return "#d68910"; // medium
  return "#2e7d32"; // low
}

function riskLabel(risk) {
  if (risk >= 0.5) return "High";
  if (risk >= 0.25) return "Medium";
  return "Low";
}

// Weight major roads more heavily than minor connectors so the primary
// corridor stays visually dominant even with many branch segments loaded.
function roadWeight(roadType) {
  if (roadType?.startsWith("trunk")) return 6;
  if (roadType?.startsWith("primary")) return 5;
  if (roadType?.startsWith("secondary")) return 3.5;
  if (roadType?.startsWith("tertiary")) return 2.5;
  return 2;
}

export default function MapView({ network, route }) {
  const { nodes, segments } = network;
  const bounds = nodes.map((n) => [n.lat, n.lng]);
  // Only named corridor towns get a pin — plain OSM intersections would
  // clutter the map with hundreds of unlabeled markers.
  const namedNodes = nodes.filter((n) => n.name);

  return (
    <MapContainer
      bounds={bounds}
      boundsOptions={{ padding: [30, 30] }}
      style={{ height: "100%", width: "100%" }}
      scrollWheelZoom
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {segments.map((seg) => (
        <Polyline
          key={seg.id}
          positions={seg.geometry.map((p) => [p.lat, p.lng])}
          pathOptions={{
            color: riskColor(seg.current_risk_score),
            weight: roadWeight(seg.road_type),
            opacity: 0.8,
          }}
        >
          <Popup>
            <div style={{ fontSize: "0.85rem", lineHeight: 1.5 }}>
              <strong>{seg.name || seg.id}</strong>
              <br />
              Road class: {seg.road_type}
              <br />
              {seg.from_node_id} &rarr; {seg.to_node_id}
              <br />
              Distance: {seg.distance_km} km
              <br />
              Est. travel time: {Math.round(seg.estimated_travel_time_min)} min
              <br />
              Terrain: {seg.terrain_type}
              <br />
              Landslide susceptibility: {seg.landslide_susceptibility}
              <br />
              Flood susceptibility: {seg.flood_susceptibility}
              <br />
              Risk: {seg.current_risk_score} ({riskLabel(seg.current_risk_score)}) &middot; Status: {seg.status}
              <div style={{ marginTop: "0.4rem", color: "#888", fontSize: "0.75rem" }}>
                Terrain is a coarse elevation-based estimate; landslide/flood
                susceptibility are not yet assessed (placeholder 0.0) — see
                backend/app/data/README.md.
              </div>
            </div>
          </Popup>
        </Polyline>
      ))}

      {route && (
        <Polyline
          key={route.route_id}
          positions={route.geometry.map((p) => [p.lat, p.lng])}
          pathOptions={{ color: "#1565c0", weight: 6, opacity: 0.9, dashArray: "8 6" }}
        />
      )}

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
    </MapContainer>
  );
}
