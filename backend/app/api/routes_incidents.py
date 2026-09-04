# POST /incidents   field-officer geo-tagged incident report
# GET  /incidents    list reported incidents
#
# Maps the reported lat/lng to the nearest RoadSegment, stores the Incident,
# then runs the same risk_engine -> reroute_service pipeline as a weather event.
