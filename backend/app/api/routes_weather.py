# POST /weather/events   demo control: set a WeatherCondition on a segment
#
# Calls simulation/weather_simulator.py to apply the change, then
# core/risk_engine.py to recompute affected RiskScores, then
# core/reroute_service.py to re-check any vehicles on that segment.
