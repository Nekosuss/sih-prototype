# Background asyncio tick loop started at app startup.
#
# On each tick: advance each active vehicle a small distance along its current
# route's geometry, update current_lat/current_lng in the StateStore, then
# call reroute_service.check(vehicle_id) so rerouting reacts automatically
# during the demo rather than needing a manual trigger.
