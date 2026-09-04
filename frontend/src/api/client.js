const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
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

export async function calculateRoute(origin, destination) {
  const res = await fetch(`${API_BASE}/routes/calculate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ origin, destination }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `/routes/calculate failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}
