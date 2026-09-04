"""
Runs the Part 8 dynamic hazard response demo scenario against the real
corridor and prints each step: NORMAL -> HAZARD -> REROUTE -> CLEARED ->
NORMAL. READ-ONLY / demonstration only — does not fabricate any route or
risk value; every number comes from the real DEM/GSI-derived segment data
plus a deterministic simulated hazard.

--- Usage ---
    cd backend
    python -m app.data.hazard_response_validation
"""
from app.core.hazard_state import build_hazard_event, combine_active_hazards_into_segment_context
from app.core.reroute_service import evaluate_route_decision
from app.core.routing_engine import build_graph
from app.data.network_loader import load_network
from app.models.hazard import HazardSeverity, HazardType


def _print_decision(label, decision):
    print(f"--- {label} ---")
    print(f"  outcome: {decision.outcome.value}")
    if decision.recommended_route is not None:
        print(f"  recommended route: {decision.recommended_route.estimated_travel_time_min} min, "
              f"{decision.recommended_route.total_distance_km} km")
    else:
        print("  recommended route: NONE")
    if decision.recommended_route_risk is not None:
        print(f"  recommended route risk: {decision.recommended_route_risk.aggregate_risk_score}")
    print(f"  eta_change_min: {decision.eta_change_min}")
    print(f"  reason: {decision.reason}")
    print()


def main():
    nodes, segments = load_network()
    graph = build_graph(nodes, segments)

    print("=== STEP 1: NORMAL (no hazard) ===")
    normal = evaluate_route_decision(graph, nodes, segments, "Bhalukpong", "Bomdila")
    _print_decision("Bhalukpong -> Bomdila (normal)", normal)

    doimara_ids = [s.id for s in segments if s.name == "Doimara-Nichiphu"]
    on_route = [sid for sid in normal.recommended_route.segment_ids if sid in doimara_ids]
    print(f"Real segment(s) on this route to target: {on_route}")
    print()

    print("=== STEP 2: SIMULATED ROAD BLOCKAGE (blocking severity) on the real segment(s) above ===")
    event = build_hazard_event(HazardType.road_blockage, HazardSeverity.blocking, on_route)
    print(f"  {event.message}")
    print()

    print("=== STEP 3: REROUTE evaluation ===")
    context = combine_active_hazards_into_segment_context([event])
    disrupted = evaluate_route_decision(
        graph, nodes, segments, "Bhalukpong", "Bomdila",
        previous_route=normal.recommended_route, segment_context=context, active_hazard_ids=[event.id],
    )
    _print_decision("Bhalukpong -> Bomdila (hazard active)", disrupted)

    print("=== STEP 4: HAZARD CLEARED -> re-evaluate ===")
    # Clearing just means the hazard is no longer included when building
    # segment_context -- there is nothing to "restore" on the segments
    # themselves (see README.md "Static vs dynamic data").
    restored = evaluate_route_decision(graph, nodes, segments, "Bhalukpong", "Bomdila")
    _print_decision("Bhalukpong -> Bomdila (hazard cleared)", restored)

    assert restored.recommended_route.node_ids == normal.recommended_route.node_ids, (
        "expected clearing the hazard to restore the original route"
    )
    print("Confirmed: clearing the hazard restores the original real route exactly.")


if __name__ == "__main__":
    main()
