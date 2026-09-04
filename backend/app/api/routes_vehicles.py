# POST /vehicles/{id}/dispatch   assign an initial route (origin -> destination)
# GET  /vehicles                 list vehicles with current position/route/status
# GET  /vehicles/{id}/history    reroute event history for one vehicle
#
# Delegates to core/routing_engine.py and core/reroute_service.py; this file
# only validates requests and shapes responses.
