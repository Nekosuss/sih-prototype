# Decides whether an active vehicle route is still the best option.
#
# Steps:
#   1. Recompute risk-weighted cost of the vehicle's remaining route.
#   2. Recompute the best route from the vehicle's current position to its
#      destination (via routing_engine).
#   3. If the alternative is meaningfully cheaper (beyond REROUTE_MARGIN) or
#      an upcoming segment crosses UNSAFE_RISK_THRESHOLD, install the new
#      route and append a RerouteEvent with a human-readable reason.
#
# Called after every weather change and every incident report.
