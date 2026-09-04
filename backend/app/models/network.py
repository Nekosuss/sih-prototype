from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    city = "city"
    town = "town"
    junction = "junction"
    mountain_pass = "mountain_pass"
    depot = "depot"


class TerrainType(str, Enum):
    """Broad terrain classification. Kept separate from hazard susceptibility
    fields below — a mountain segment is not automatically landslide-prone,
    and a plain segment can still be flood-prone. See RoadSegment."""

    plain = "plain"
    hill = "hill"
    mountain = "mountain"


class RoadType(str, Enum):
    """Mirrors OSM `highway` tag values for the road classes this corridor's
    network is built from (see backend/app/data/README.md) — not a
    hand-invented classification."""

    trunk = "trunk"
    trunk_link = "trunk_link"
    primary = "primary"
    primary_link = "primary_link"
    secondary = "secondary"
    secondary_link = "secondary_link"
    tertiary = "tertiary"
    tertiary_link = "tertiary_link"
    unclassified = "unclassified"


class SegmentStatus(str, Enum):
    open = "open"
    restricted = "restricted"
    closed = "closed"


class GeoPoint(BaseModel):
    lat: float
    lng: float


class Node(BaseModel):
    id: str
    # Optional: most OSM road-network nodes are plain intersections with no
    # place name. Named corridor towns (Guwahati, Tezpur, ...) still have one.
    name: Optional[str] = None
    lat: float
    lng: float
    type: NodeType
    elevation_m: Optional[float] = None


class RoadSegment(BaseModel):
    id: str
    from_node_id: str
    to_node_id: str
    name: Optional[str] = None
    road_type: RoadType
    distance_km: float
    estimated_travel_time_min: float
    geometry: list[GeoPoint]

    # Real OSM provenance, preserved as-is (not derived/normalized) so the
    # original tag values are always inspectable. See
    # backend/app/data/osm_geojson_loader.py.
    ref: Optional[str] = None  # e.g. "NH13"
    oneway: Optional[str] = None  # raw OSM oneway tag: "yes" | "no" | "reversible" | None
    maxspeed: Optional[str] = None  # raw OSM maxspeed tag, e.g. "40"

    # Derived from the above, consumed by routing_engine.build_graph() to
    # decide whether this segment becomes one directed edge or two. True
    # unless oneway is exactly "yes" — see build_graph() and
    # osm_geojson_loader.py for why only that exact value is treated as
    # one-way.
    bidirectional: bool = True
    # The speed actually used to compute estimated_travel_time_min: parsed
    # from maxspeed where present, otherwise a highway-class default
    # assumption (see osm_geojson_loader.DEFAULT_SPEED_KPH). Exposed so the
    # assumption is never hidden inside a pre-computed travel time.
    assumed_speed_kph: Optional[float] = None

    # Terrain and hazard susceptibility are intentionally separate concepts:
    # terrain_type is a coarse physical classification, while the
    # susceptibility fields are independent 0-1 scores. A future risk model
    # (rule-based or ML) reads all of these as separate features rather than
    # one collapsed category. See ARCHITECTURE.md section 6.
    #
    # elevation_m/slope_deg (Part 4.8): real per-segment terrain features
    # sampled from a DEM (see app/data/dem_loader.py, dem_processor.py) —
    # elevation_m is the mean of DEM samples along the segment's geometry,
    # slope_deg is the mean absolute gradient magnitude over that sampled
    # profile (degrees, always >= 0 — a magnitude, not a directional
    # grade; see dem_processor.py's module docstring for why a naive
    # endpoint-to-endpoint slope is misleading on undulating mountain
    # roads). slope_deg is None if the DEM had fewer than 2 usable sample
    # points for this segment (e.g. it fell outside cached tile coverage).
    # These are TERRAIN features only — not a hazard/landslide probability;
    # base_risk/current_risk_score do not read slope_deg yet. See
    # backend/app/data/README.md "DEM provenance" for source/resolution/CRS.
    terrain_type: TerrainType
    slope_deg: Optional[float] = None
    elevation_m: Optional[float] = None
    landslide_susceptibility: float = Field(ge=0.0, le=1.0)
    flood_susceptibility: float = Field(ge=0.0, le=1.0)

    base_risk: float = Field(ge=0.0, le=1.0)
    status: SegmentStatus = SegmentStatus.open
    current_risk_score: float = Field(ge=0.0, le=1.0)

    # Historical GSI landslide observations spatially joined to this segment
    # (see data/landslide_mapper.py). These are raw historical FEATURES, not
    # a risk score or a probability — nothing derives base_risk/
    # current_risk_score from them yet, and the baseline routing cost
    # (core/routing_engine.py::edge_cost) still uses travel time only.
    historical_landslide_count: int = 0
    nearest_landslide_distance_m: Optional[float] = None

    # Part 11: landslide/flood HAZARD ZONATION (susceptibility) from an
    # official spatial layer -- see app/data/hazard_layer_loader.py for the
    # "historical occurrence vs. susceptibility zonation" distinction and
    # this project's verified APSAC/SRSAC data-access status. Deliberately
    # SEPARATE fields from historical_landslide_count/nearest_landslide_distance_m
    # above (never overwritten by this) and from the older Part 2
    # landslide_susceptibility/flood_susceptibility placeholders below
    # (also never overwritten -- those remain the pre-Part-11 uniformly-0.0
    # "not assessed" fields). None means "no official zonation layer
    # currently covers this segment" -- NEVER coerced to a fabricated
    # 0.0/"low". *_class preserves the source layer's own class string
    # verbatim (e.g. "High"); *_score is that class mapped through
    # app/config.py::HAZARD_CLASS_TO_SCORE to a normalized [0,1] value.
    landslide_hazard_class: Optional[str] = None
    landslide_hazard_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    flood_hazard_class: Optional[str] = None
    flood_hazard_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Per-hazard-type provenance string (e.g. "real: APSAC landslide hazard
    # zonation"), populated only for whichever of the two above is actually
    # non-None -- parallel to the generic `source` dict below but kept
    # separate since it's specific to Part 11's two hazard-layer fields.
    hazard_layer_source: dict[str, str] = Field(default_factory=dict)

    # OSM way id(s) this segment was built from (a simplified/consolidated
    # edge can represent more than one original way). Empty for any segment
    # not sourced from OSM.
    osm_way_ids: list[int] = Field(default_factory=list)

    # Per-field provenance (real vs. prototype-placeholder). See
    # backend/app/data/README.md for the full explanation.
    source: dict[str, str] = Field(default_factory=dict)
