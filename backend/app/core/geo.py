"""Small shared geo utilities. No framework/graph/model dependencies."""
import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def interpolate_along_path(points: list[tuple[float, float]], target_distance_km: float) -> tuple[float, float]:
    """
    Part 9 (vehicle simulation): given a real polyline as [(lat, lng), ...]
    (e.g. a road segment's or a whole route's actual geometry) and a target
    distance travelled along it, returns the (lat, lng) at that point —
    walking the REAL vertices and linearly interpolating between the two
    that bracket target_distance_km, never a straight line between just the
    path's two endpoints (unless it only has two points to begin with).
    target_distance_km is clamped to the path's own length: <= 0 returns
    the first point, >= total length returns the last point.
    """
    if not points:
        raise ValueError("interpolate_along_path: empty path")
    if len(points) == 1 or target_distance_km <= 0:
        return points[0]

    cumulative = 0.0
    for i in range(len(points) - 1):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        seg_len = haversine_km(lat1, lon1, lat2, lon2)
        if cumulative + seg_len >= target_distance_km or i == len(points) - 2:
            remaining = target_distance_km - cumulative
            fraction = 0.0 if seg_len <= 0 else min(1.0, max(0.0, remaining / seg_len))
            return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)
        cumulative += seg_len
    return points[-1]


# ---------------------------------------------------------------------------
# Part 12: nearest-point-on-real-road-geometry (field-report GPS matching).
# ---------------------------------------------------------------------------


def _closest_point_on_segment(
    lat: float, lng: float, lat1: float, lng1: float, lat2: float, lng2: float
) -> tuple[float, float]:
    """
    The closest point to (lat, lng) on the straight line piece from
    (lat1, lng1) to (lat2, lng2) -- one real polyline edge, never a whole
    route/segment collapsed to its two endpoints. Uses a local equirectangular
    (flat-earth) projection centered on the edge's own latitude: accurate
    enough for a single short real road-geometry edge (a few hundred meters
    at most on this corridor's data), the same kind of approximation
    haversine_km's callers already rely on elsewhere in this module for
    short real distances. Returns real (lat, lng), not projected coordinates.
    """
    lat0 = (lat1 + lat2) / 2.0
    km_per_deg_lat = 111.32
    km_per_deg_lng = 111.32 * math.cos(math.radians(lat0))

    x1, y1 = lng1 * km_per_deg_lng, lat1 * km_per_deg_lat
    x2, y2 = lng2 * km_per_deg_lng, lat2 * km_per_deg_lat
    xp, yp = lng * km_per_deg_lng, lat * km_per_deg_lat

    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        return lat1, lng1

    t = ((xp - x1) * dx + (yp - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    x, y = x1 + t * dx, y1 + t * dy
    return y / km_per_deg_lat, x / km_per_deg_lng


def nearest_point_on_polyline(lat: float, lng: float, points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """
    Part 12 (field reporting): given a real polyline as [(lat, lng), ...]
    (e.g. a road segment's actual OSM geometry) and a query point, returns
    (nearest_lat, nearest_lng, distance_km) -- the true nearest point on any
    of the polyline's real consecutive edges, checked against every edge
    (never just the vertices, never a straight line between only the first
    and last point unless the polyline only has two points to begin with).
    distance_km is a real haversine distance from the query point to that
    nearest point, not the flat-earth-projected distance used internally to
    pick it.
    """
    if not points:
        raise ValueError("nearest_point_on_polyline: empty path")
    if len(points) == 1:
        lat0, lng0 = points[0]
        return lat0, lng0, haversine_km(lat, lng, lat0, lng0)

    best: tuple[float, float, float] | None = None
    for (lat1, lng1), (lat2, lng2) in zip(points, points[1:]):
        clat, clng = _closest_point_on_segment(lat, lng, lat1, lng1, lat2, lng2)
        distance_km = haversine_km(lat, lng, clat, clng)
        if best is None or distance_km < best[2]:
            best = (clat, clng, distance_km)
    return best
