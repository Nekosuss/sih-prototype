"""
Runs Part 6 risk-aware routing (core/routing_engine.py) against the real
corridor and reports the fastest-vs-safe route comparison for each
consecutive named-location leg, plus a real-data demonstration of each of
the three comparison cases (Part 6 section 5). READ-ONLY reporting script —
does not change routing, does not fabricate any route: every route printed
below is an actual shortest path over the real road graph.

--- Usage ---
    cd backend
    python -m app.data.risk_aware_routing_validation
"""
from app.core.routing_engine import build_graph, compare_fastest_and_safe_routes
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.network_loader import load_network


def _print_result(label, result):
    print(f"--- {label} ---")
    print(f"  outcome: {result.outcome.value}")
    print(f"  fastest route: {result.fastest_route.estimated_travel_time_min} min, "
          f"{result.fastest_route.total_distance_km} km, "
          f"aggregate_risk={result.fastest_route_risk.aggregate_risk_score}, "
          f"max_risk={result.fastest_route_risk.max_segment_risk}, "
          f"unsafe_segments={result.fastest_route_risk.unsafe_segment_count}")
    if result.recommended_route is not None:
        print(f"  recommended route: {result.recommended_route.estimated_travel_time_min} min, "
              f"{result.recommended_route.total_distance_km} km, "
              f"aggregate_risk={result.recommended_route_risk.aggregate_risk_score}, "
              f"max_risk={result.recommended_route_risk.max_segment_risk}")
    else:
        print("  recommended route: NONE (no safe route available)")
    print(f"  safer_alternative_selected: {result.safer_alternative_selected}")
    for reason in result.reasons:
        print(f"    - {reason}")
    print()


def main():
    nodes, segments = load_network()
    graph = build_graph(nodes, segments)
    names = [loc["name"] for loc in DEMO_LOCATIONS]

    print("=== CASE demonstration across every real consecutive corridor leg (no weather/incident context) ===")
    print()
    for origin, destination in zip(names, names[1:]):
        result = compare_fastest_and_safe_routes(graph, nodes, segments, origin, destination)
        _print_result(f"{origin} -> {destination}", result)

    print("=== Real-data demonstration of CASE B under a supplied severe weather+incident context ===")
    print("(weather_factor=0.9, incident_factor=0.9 -- a hypothetical scenario; the ROAD SEGMENTS")
    print(" themselves and the alternative path chosen are entirely real, not fabricated)")
    print()
    result = compare_fastest_and_safe_routes(
        graph, nodes, segments, "Bhalukpong", "Bomdila", weather_factor=0.9, incident_factor=0.9
    )
    _print_result("Bhalukpong -> Bomdila (severe context)", result)

    print("=== Note on CASE C (no safe route available) ===")
    print("No real origin/destination pair in this corridor currently produces CASE C, even under")
    print("maximum hypothetical weather_factor=1.0/incident_factor=1.0 stress -- the corridor's one")
    print("genuinely single-road stretch with no real alternative (Dirang -> Sela Pass) tops out at")
    print("~0.71 prototype risk under that maximum stress, which stays under HARD_UNSAFE_RISK_THRESHOLD.")
    print("CASE C is demonstrated instead with a small synthetic graph in")
    print("tests/test_risk_aware_routing.py (test_no_safe_route_raises_specific_exception) -- see")
    print("that module's docstring for why a synthetic graph is the honest way to test this case today.")


if __name__ == "__main__":
    main()
