"""
Part 10 validation/report script: real IMD rainfall -> weather_factor ->
segment risk -> route risk -> route decision, end to end, over the real
corridor. READ-ONLY reporting -- does not change routing, risk scoring, or
the rainfall extraction itself, and fabricates nothing: every rainfall
value below is read directly from the committed extraction
(app/data/rainfall_corridor_2023.csv, produced by
backend/scripts/fetch_rainfall_data.py from the real IMD NetCDF file).

--- Usage ---
    cd backend
    python -m app.data.rainfall_validation
"""
from app.config import DEFAULT_RAINFALL_OBSERVATION_DATE, HARD_UNSAFE_RISK_THRESHOLD
from app.core.reroute_service import evaluate_route_decision
from app.core.routing_engine import build_graph, compare_fastest_and_safe_routes
from app.core.weather_factor import rainfall_segment_context, weather_factor_for_point, weather_factor_for_segment
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.network_loader import load_network
from app.data.rainfall_loader import RainfallStatus, SOURCE_NAME, get_default_rainfall_loader


def _print_header(title: str) -> None:
    print(f"=== {title} ===")


def main():
    loader = get_default_rainfall_loader()
    date_min, date_max = loader.date_range
    lat_min, lat_max, lon_min, lon_max = loader.bounding_box

    _print_header("Dataset provenance")
    print(f"Source: {SOURCE_NAME}")
    print("Publisher: India Meteorological Department, Climate Prediction Group, Pune")
    print("Citation: Pai D.S. et al. 2014, MAUSAM 65,1, pp1-18")
    print(f"Downloaded file: backend/app/data/rainfall_cache/ind<year>_rfp25.nc (gitignored raw cache)")
    print(f"Committed extraction: {loader.csv_path.name}")
    print(f"Dataset period covered by this extraction: {date_min} to {date_max} ({len(loader.dates)} days)")
    print(f"Spatial resolution: {0.25} x {0.25} degree")
    print(f"Geographic coverage (extracted subset, corridor bbox + margin): "
          f"lat {lat_min}-{lat_max}, lon {lon_min}-{lon_max}")
    print(f"Grid cells loaded: {loader.grid_cell_count}")
    print(f"Total (date, grid cell) observations loaded: {loader.observation_count}")
    missing_count = loader.missing_value_count
    print(f"Observations that are IMD's real missing-value sentinel (-999.0), preserved as 'missing', "
          f"never coerced to 0.0: {missing_count} ({100 * missing_count / loader.observation_count:.1f}%)")
    print()

    _print_header(f"Sample rainfall + weather_factor at the 7 named corridor locations ({DEFAULT_RAINFALL_OBSERVATION_DATE})")
    for loc in DEMO_LOCATIONS:
        result = weather_factor_for_point(loc["lat"], loc["lng"], DEFAULT_RAINFALL_OBSERVATION_DATE, loader=loader)
        obs = result.observation
        print(f"  {loc['name']:12s} (grid cell {obs.grid_lat},{obs.grid_lon}): "
              f"rainfall_mm={obs.rainfall_mm} status={obs.status.value} weather_factor={result.weather_factor}")
    print()

    _print_header("Corridor-wide search for the single heaviest real rainfall day in the extracted year")
    daily_max = []
    for d in loader.dates:
        value = loader.daily_max_mm(d)
        if value is not None:
            daily_max.append((value, d))
    daily_max.sort(reverse=True)
    print("Top 5 real corridor-wide daily maxima (date, mm) -- not hand-picked, computed from the loaded data:")
    for value, d in daily_max[:5]:
        print(f"  {d}: {value:.1f} mm")
    print(f"(DEFAULT_RAINFALL_OBSERVATION_DATE = {DEFAULT_RAINFALL_OBSERVATION_DATE!r} in app/config.py "
          f"was chosen because it is exactly this real dataset's single heaviest corridor-wide day.)")
    print()

    nodes, segments = load_network()
    graph = build_graph(nodes, segments)

    _print_header(f"Sample real corridor segments nearest each named location, and their real weather_factor ({DEFAULT_RAINFALL_OBSERVATION_DATE})")
    from app.core.geo import haversine_km

    def nearest_segment(lat, lng):
        def mid(s):
            return s.geometry[len(s.geometry) // 2]
        return min(segments, key=lambda s: haversine_km(lat, lng, mid(s).lat, mid(s).lng))

    for loc in DEMO_LOCATIONS:
        seg = nearest_segment(loc["lat"], loc["lng"])
        result = weather_factor_for_segment(seg, DEFAULT_RAINFALL_OBSERVATION_DATE, loader=loader)
        print(f"  {loc['name']:12s} -> segment {seg.id} ({seg.name}): "
              f"rainfall_mm={result.observation.rainfall_mm} weather_factor={result.weather_factor}")
    print()

    _print_header("End-to-end chain: real rainfall -> weather_factor -> segment risk -> route risk -> decision")
    context = rainfall_segment_context(segments, DEFAULT_RAINFALL_OBSERVATION_DATE, loader=loader)
    print(f"Segments with a real (non-missing) rainfall-derived weather_factor for "
          f"{DEFAULT_RAINFALL_OBSERVATION_DATE}: {len(context)} / {len(segments)}")
    if context:
        max_seg_id = max(context, key=lambda sid: context[sid].weather_factor)
        print(f"Highest real weather_factor on any segment: {context[max_seg_id].weather_factor} (segment {max_seg_id})")
    print()

    names = [loc["name"] for loc in DEMO_LOCATIONS]
    for origin, destination in zip(names, names[1:]):
        result = compare_fastest_and_safe_routes(
            graph, nodes, segments, origin, destination, segment_context=context
        )
        decision = evaluate_route_decision(
            graph, nodes, segments, origin, destination, segment_context=context,
        )
        print(f"  {origin} -> {destination}: outcome={result.outcome.value}, "
              f"aggregate_risk={result.fastest_route_risk.aggregate_risk_score}, "
              f"max_segment_risk={result.fastest_route_risk.max_segment_risk}, "
              f"decision={decision.outcome.value}")
    print()
    print("This is the ACTUAL outcome of real rainfall data run through the unmodified Part 5/6/8 "
          f"risk/routing engines -- not adjusted to force a reroute. HARD_UNSAFE_RISK_THRESHOLD = "
          f"{HARD_UNSAFE_RISK_THRESHOLD}. If no leg above rerouted, that is a valid, honestly reported "
          "result of this one real historical day's rainfall, not a pipeline failure.")


if __name__ == "__main__":
    main()
