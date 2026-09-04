"""
Part 11 validation/report script: landslide + flood hazard-ZONATION layers.

--- Usage ---
    cd backend
    python -m app.data.hazard_layer_validation

READ-ONLY reporting -- does not change the loader, the risk engine, or any
routing behavior. Reports exactly what is real vs. unavailable; fabricates
nothing. If the official APSAC layer is unavailable (the current, verified
state -- see app/data/hazard_layer_loader.py's module docstring), this
script says so explicitly rather than inventing sample results.
"""
from app.config import HAZARD_CLASS_TO_SCORE, HAZARD_LEVEL_THRESHOLDS, HAZARD_SEGMENT_SAMPLE_FRACTIONS
from app.core.hazard_layer_service import segment_flood_hazard, segment_landslide_hazard
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.hazard_layer_loader import get_default_hazard_layer_loader
from app.data.network_loader import load_network


def _print_header(title: str) -> None:
    print(f"=== {title} ===")


def main():
    loader = get_default_hazard_layer_loader()

    _print_header("Dataset provenance")
    print("Primary official source: Arunachal Pradesh State Remote Sensing Application")
    print("Centre (APSAC/SRSAC) -- https://www.srsac.arunachal.gov.in/admin/geospatial.html")
    print("(mirror: https://www.srsac.arunachal.gov.in/geospatial.php)")
    print("Catalogued layers: Landslide Hazard Zonation Map (1:50K state-wide; 1:10K for")
    print("Tawang/West Kameng/East Kameng/Pakke-Kessang/Papumpare -- this project's corridor")
    print("districts), Flood Hazard Zonation Map (1:25K, state-wide).")
    print()
    print("Verified access status (checked directly against both live pages): NEITHER page")
    print("offers a direct download link or file -- both require a manual data request/")
    print("contact-form submission. The 1:10K corridor-district landslide zonation was itself")
    print("listed as still in progress (\"Database will be ready by June, 2024\") at the time")
    print("this was checked.")
    print()
    print(f"Landslide layer loaded from disk: {loader.landslide_layer.is_loaded} "
          f"(expected path: {loader.landslide_layer.path})")
    print(f"Flood layer loaded from disk: {loader.flood_layer.is_loaded} "
          f"(expected path: {loader.flood_layer.path})")
    print(f"Landslide layer feature count: {loader.landslide_layer.feature_count}")
    print(f"Flood layer feature count: {loader.flood_layer.feature_count}")
    print()
    if not loader.landslide_layer.is_loaded and not loader.flood_layer.is_loaded:
        print("Official production layer not locally available; spatial lookup validated")
        print("using synthetic test geometries only (see tests/test_hazard_layer.py).")
    print()

    _print_header("Source classification -> normalized score mapping (app/config.py)")
    for source_class, score in HAZARD_CLASS_TO_SCORE.items():
        print(f"  {source_class!r:12s} -> {score}")
    print(f"Normalized-score -> display level thresholds: {HAZARD_LEVEL_THRESHOLDS}")
    print("(This is the standard NDMA/BIS-style Very Low/Low/Moderate/High/Very High")
    print(" hazard-zonation vocabulary, not an APSAC-specific scale -- see app/config.py.)")
    print()

    _print_header("Geographic coverage / CRS")
    print("CRS: reprojected to EPSG:4326 on load, whatever the source file's native CRS is")
    print("(see HazardPolygonLayer.__init__). Geographic coverage: NONE currently -- no real")
    print("file is loaded (see above).")
    print()

    nodes, segments = load_network()
    total = len(segments)
    with_landslide = sum(1 for s in segments if s.landslide_hazard_score is not None)
    with_flood = sum(1 for s in segments if s.flood_hazard_score is not None)

    _print_header("Corridor coverage (precomputed RoadSegment fields)")
    print(f"Total real road segments: {total}")
    print(f"Segments with real landslide hazard-zonation data: {with_landslide} ({100 * with_landslide / total:.1f}%)")
    print(f"Segments with real flood hazard-zonation data: {with_flood} ({100 * with_flood / total:.1f}%)")
    print(f"Segment sampling for spatial lookup: {len(HAZARD_SEGMENT_SAMPLE_FRACTIONS)} points per segment "
          f"at fractions {HAZARD_SEGMENT_SAMPLE_FRACTIONS}, aggregated by conservative maximum.")
    print()

    _print_header("Sample results at the 7 named corridor locations (live query against the real loader)")
    from app.core.geo import haversine_km

    def nearest_segment(lat, lng):
        def mid(s):
            return s.geometry[len(s.geometry) // 2]
        return min(segments, key=lambda s: haversine_km(lat, lng, mid(s).lat, mid(s).lng))

    for loc in DEMO_LOCATIONS:
        seg = nearest_segment(loc["lat"], loc["lng"])
        landslide = segment_landslide_hazard(seg, loader=loader)
        flood = segment_flood_hazard(seg, loader=loader)
        print(
            f"  {loc['name']:12s} -> segment {seg.id}: "
            f"landslide={landslide.status.value}"
            + (f" ({landslide.hazard_class}, score={landslide.hazard_score})" if landslide.status.value == "ok" else "")
            + f", flood={flood.status.value}"
            + (f" ({flood.hazard_class}, score={flood.hazard_score})" if flood.status.value == "ok" else "")
        )
    print()
    print("Every 'no_coverage' result above is the honest, real outcome of querying the")
    print("actual (currently unloaded) loader -- none of these values are fabricated placeholders.")


if __name__ == "__main__":
    main()
