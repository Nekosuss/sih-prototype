"""
Smoke tests for the network/routing API: confirms the full
network -> graph -> backend -> API pipeline actually serves the real OSM
dataset (not just that the underlying Python objects are correct, tested
separately in test_network.py / test_routing.py).
"""
import pytest
from fastapi.testclient import TestClient

from app.core.routing_engine import resolve_location
from app.data.network_loader import load_network
from app.main import app
from app.store.state_store import state_store


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    state_store.load()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def bhalukpong_id():
    nodes, _ = load_network()
    return resolve_location(nodes, "Bhalukpong").id


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["segments_loaded"] > 500


def test_get_network(client):
    resp = client.get("/network")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) > 500
    assert len(body["segments"]) > 500
    named = {n["name"] for n in body["nodes"] if n["name"]}
    assert "Guwahati" in named and "Tawang" in named


def test_list_segments(client, bhalukpong_id):
    resp = client.get("/segments")
    assert resp.status_code == 200
    segs = resp.json()
    assert any(s["from_node_id"] == bhalukpong_id or s["to_node_id"] == bhalukpong_id for s in segs)


def test_get_segment_detail(client):
    resp = client.get("/segments")
    seg_id = resp.json()[0]["id"]

    detail = client.get(f"/segments/{seg_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == seg_id
    assert "road_type" in body and "oneway" in body and "ref" in body


def test_get_segment_detail_unknown_returns_404(client):
    resp = client.get("/segments/does_not_exist")
    assert resp.status_code == 404


def test_get_segment_risk(client):
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/segments/{seg_id}/risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == seg_id
    assert 0.0 <= body["current_risk_score"] <= 1.0


def test_calculate_route(client):
    resp = client.post("/routes/calculate", json={"origin": "Guwahati", "destination": "Tawang"})
    assert resp.status_code == 200
    body = resp.json()
    route = body["route"]
    assert route["node_ids"][0] == route["origin"]
    assert route["node_ids"][-1] == route["destination"]
    assert len(route["segment_ids"]) > 0
    assert route["total_distance_km"] > 0
    assert route["estimated_travel_time_min"] > 0
    assert len(route["geometry"]) > 100


def test_calculate_route_with_coordinates(client):
    resp = client.post(
        "/routes/calculate",
        json={"origin": {"lat": 26.19, "lng": 91.75}, "destination": {"lat": 27.60, "lng": 91.87}},
    )
    assert resp.status_code == 200
    route = resp.json()["route"]
    assert route["total_distance_km"] > 0


def test_calculate_route_unknown_origin_returns_400(client):
    resp = client.post("/routes/calculate", json={"origin": "Atlantis", "destination": "Tawang"})
    assert resp.status_code == 400


def test_alternative_routes_available_reflects_real_branching(client):
    """Tezpur -> Bhalukpong genuinely has an alternative in this dataset;
    Dirang -> Sela Pass genuinely doesn't (see test_routing.py case A/B)."""
    has_alt = client.post("/routes/calculate", json={"origin": "Tezpur", "destination": "Bhalukpong"})
    assert has_alt.json()["alternative_routes_available"] is True

    no_alt = client.post("/routes/calculate", json={"origin": "Dirang", "destination": "Sela Pass"})
    assert no_alt.json()["alternative_routes_available"] is False


def test_get_route_round_trip(client):
    calc = client.post("/routes/calculate", json={"origin": "Bhalukpong", "destination": "Sela Pass"})
    route_id = calc.json()["route"]["route_id"]

    resp = client.get(f"/routes/{route_id}")
    assert resp.status_code == 200
    assert resp.json()["route_id"] == route_id
    assert resp.json()["node_ids"] == calc.json()["route"]["node_ids"]


def test_get_route_unknown_returns_404(client):
    resp = client.get("/routes/route_doesnotexist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Part 6: /routes/calculate-risk-aware
# ---------------------------------------------------------------------------


def test_calculate_risk_aware_route(client):
    resp = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] in ("fastest_route_is_safe", "safer_route_selected", "no_safe_route_available")
    assert body["fastest_route"]["total_distance_km"] > 0
    assert 0.0 <= body["fastest_route_risk"]["aggregate_risk_score"] <= 1.0
    assert 0.0 <= body["fastest_route_risk"]["max_segment_risk"] <= 1.0
    for reason in body["reasons"]:
        assert "probability" not in reason.lower() or "not a calibrated probability" in reason.lower()


def test_calculate_risk_aware_route_with_weather_context_selects_real_alternative(client):
    """Real-data CASE B: under a supplied severe weather+incident context,
    Bhalukpong -> Bomdila's fastest route crosses the hard unsafe threshold
    and a genuine (not fabricated) alternative is recommended instead."""
    resp = client.post(
        "/routes/calculate-risk-aware",
        json={"origin": "Bhalukpong", "destination": "Bomdila", "weather_factor": 0.9, "incident_factor": 0.9},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "safer_route_selected"
    assert body["safer_alternative_selected"] is True
    assert body["unsafe_segments_in_fastest_route"] is True
    assert body["recommended_route"] is not None
    assert body["recommended_route"]["node_ids"] != body["fastest_route"]["node_ids"]


def test_calculate_risk_aware_route_unknown_origin_returns_400(client):
    resp = client.post("/routes/calculate-risk-aware", json={"origin": "Atlantis", "destination": "Tawang"})
    assert resp.status_code == 400


def test_calculate_risk_aware_route_weather_factor_out_of_range_rejected(client):
    resp = client.post(
        "/routes/calculate-risk-aware",
        json={"origin": "Guwahati", "destination": "Tawang", "weather_factor": 5.0},
    )
    assert resp.status_code == 422  # Pydantic field validation (ge=0, le=1)


def test_calculate_risk_aware_route_exposes_per_segment_risk(client):
    """Frontend integration (Part 6.5): segment-level risk for map hover/click
    must be present in the API response, matching the route's own segment_ids."""
    resp = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"})
    body = resp.json()
    fastest_ids = body["fastest_route"]["segment_ids"]
    segment_risks = body["fastest_route_segment_risks"]
    assert len(segment_risks) == len(fastest_ids)
    assert [r["segment_id"] for r in segment_risks] == fastest_ids
    first = segment_risks[0]
    assert 0.0 <= first["risk_score"] <= 1.0
    assert first["risk_level"] in ("low", "moderate", "high", "critical")
    assert set(first["breakdown"].keys()) == {"slope_risk", "historical_landslide_risk", "weather_risk", "incident_risk"}
    assert isinstance(first["reasons"], list) and len(first["reasons"]) > 0


# ---------------------------------------------------------------------------
# Part 8: /hazards and /routes/evaluate-disruption
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_hazards():
    """These tests mutate the shared (module-scoped) state_store's hazard
    list via the API -- reset it after each test so hazard tests never leak
    into each other or into unrelated tests later in this module."""
    yield
    state_store.reset_hazards()


def test_simulate_hazard_creates_event(client, clean_hazards):
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.post(
        "/hazards/simulate",
        json={"type": "heavy_rain", "severity": "major", "affected_segment_ids": [seg_id]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "heavy_rain"
    assert body["active"] is True
    assert "SIMULATED" in body["message"].upper()
    assert body["affected_segment_ids"] == [seg_id]


def test_simulate_hazard_unknown_segment_returns_400(client, clean_hazards):
    resp = client.post(
        "/hazards/simulate",
        json={"type": "heavy_rain", "severity": "major", "affected_segment_ids": ["seg_does_not_exist"]},
    )
    assert resp.status_code == 400


def test_simulate_hazard_empty_segment_list_rejected(client, clean_hazards):
    resp = client.post(
        "/hazards/simulate",
        json={"type": "heavy_rain", "severity": "major", "affected_segment_ids": []},
    )
    assert resp.status_code == 422


def test_list_hazards_active_only_default(client, clean_hazards):
    seg_id = client.get("/segments").json()[0]["id"]
    created = client.post(
        "/hazards/simulate",
        json={"type": "landslide", "severity": "minor", "affected_segment_ids": [seg_id]},
    ).json()

    active = client.get("/hazards").json()
    assert any(h["id"] == created["id"] for h in active)

    client.post(f"/hazards/{created['id']}/clear")
    active_after_clear = client.get("/hazards").json()
    assert not any(h["id"] == created["id"] for h in active_after_clear)

    all_hazards = client.get("/hazards", params={"active_only": False}).json()
    assert any(h["id"] == created["id"] for h in all_hazards)


def test_clear_unknown_hazard_returns_404(client, clean_hazards):
    resp = client.post("/hazards/does_not_exist/clear")
    assert resp.status_code == 404


def test_hazards_reset_endpoint(client, clean_hazards):
    seg_id = client.get("/segments").json()[0]["id"]
    client.post("/hazards/simulate", json={"type": "heavy_rain", "severity": "minor", "affected_segment_ids": [seg_id]})
    resp = client.post("/hazards/reset")
    assert resp.status_code == 200
    assert client.get("/hazards", params={"active_only": False}).json() == []


def test_evaluate_disruption_real_corridor_reroutes_after_hazard(client, clean_hazards):
    """End-to-end real-OSM demo scenario via the actual HTTP API: normal ->
    hazard -> reroute -> clear -> normal again (Part 8 section 11)."""
    segs = client.get("/segments").json()
    on_route = [s["id"] for s in segs if s["name"] == "Doimara-Nichiphu"]
    assert on_route

    baseline = client.post("/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}).json()
    assert baseline["outcome"] == "continue"

    hazard = client.post(
        "/hazards/simulate",
        json={"type": "road_blockage", "severity": "blocking", "affected_segment_ids": on_route},
    ).json()

    disrupted = client.post(
        "/routes/evaluate-disruption",
        json={
            "origin": "Bhalukpong",
            "destination": "Bomdila",
            "previous_route_id": baseline["recommended_route"]["route_id"],
        },
    ).json()
    assert disrupted["outcome"] == "reroute"
    assert disrupted["recommended_route"] is not None
    assert set(disrupted["recommended_route"]["segment_ids"]).isdisjoint(on_route)
    assert hazard["id"] in disrupted["active_hazard_ids"]

    client.post(f"/hazards/{hazard['id']}/clear")

    restored = client.post("/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}).json()
    assert restored["outcome"] == "continue"


def test_segment_risk_aware_reflects_active_hazard_then_reverts_on_clear(client, clean_hazards):
    seg_id = client.get("/segments").json()[0]["id"]

    before = client.get(f"/segments/{seg_id}/risk-aware").json()
    assert before["breakdown"]["weather_risk"] == 0.0

    hazard = client.post(
        "/hazards/simulate",
        json={"type": "heavy_rain", "severity": "major", "affected_segment_ids": [seg_id]},
    ).json()

    after = client.get(f"/segments/{seg_id}/risk-aware").json()
    assert after["breakdown"]["weather_risk"] > before["breakdown"]["weather_risk"]
    assert after["risk_score"] > before["risk_score"]

    client.post(f"/hazards/{hazard['id']}/clear")
    reverted = client.get(f"/segments/{seg_id}/risk-aware").json()
    assert reverted["breakdown"]["weather_risk"] == 0.0
    assert reverted["risk_score"] == before["risk_score"]


def test_segment_risk_aware_unknown_segment_returns_404(client, clean_hazards):
    resp = client.get("/segments/does_not_exist/risk-aware")
    assert resp.status_code == 404


def test_evaluate_disruption_unknown_origin_returns_400(client, clean_hazards):
    resp = client.post("/routes/evaluate-disruption", json={"origin": "Atlantis", "destination": "Tawang"})
    assert resp.status_code == 400


def test_risk_aware_route_is_retrievable_by_id(client):
    calc = client.post("/routes/calculate-risk-aware", json={"origin": "Bhalukpong", "destination": "Sela Pass"})
    route_id = calc.json()["fastest_route"]["route_id"]

    resp = client.get(f"/routes/{route_id}")
    assert resp.status_code == 200
    assert resp.json()["route_id"] == route_id


# ---------------------------------------------------------------------------
# Part 9: /vehicles -- deterministic simulated vehicle movement
# ---------------------------------------------------------------------------


def test_create_vehicle_starts_idle_with_real_route(client):
    resp = client.post("/vehicles", json={"name": "Truck 1", "origin": "Guwahati", "destination": "Tezpur"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "idle"
    assert body["current_route"]["total_distance_km"] > 0
    assert body["current_lat"] is not None and body["current_lng"] is not None
    assert body["progress"] == 0.0
    assert "not live GPS" in body["methodology_note"]


def test_create_vehicle_unknown_origin_returns_400(client):
    resp = client.post("/vehicles", json={"name": "Truck 1", "origin": "Atlantis", "destination": "Tezpur"})
    assert resp.status_code == 400


def test_vehicle_moves_after_start_when_polled(client):
    import time

    created = client.post("/vehicles", json={"name": "Truck 1", "origin": "Guwahati", "destination": "Tezpur"}).json()
    client.post(f"/vehicles/{created['id']}/start")
    time.sleep(1.2)
    polled = client.get(f"/vehicles/{created['id']}").json()
    assert polled["status"] == "en_route"
    assert polled["distance_travelled_km"] > 0
    assert polled["progress"] > 0


def test_vehicle_pause_freezes_position(client):
    import time

    created = client.post("/vehicles", json={"name": "Truck 1", "origin": "Guwahati", "destination": "Tezpur"}).json()
    client.post(f"/vehicles/{created['id']}/start")
    time.sleep(0.5)
    paused = client.post(f"/vehicles/{created['id']}/pause").json()
    assert paused["paused"] is True
    frozen_progress = paused["progress"]
    time.sleep(1.0)
    still = client.get(f"/vehicles/{created['id']}").json()
    assert still["progress"] == frozen_progress


def test_vehicle_reset_returns_to_idle(client):
    created = client.post("/vehicles", json={"name": "Truck 1", "origin": "Guwahati", "destination": "Tezpur"}).json()
    client.post(f"/vehicles/{created['id']}/start")
    reset = client.post(f"/vehicles/{created['id']}/reset").json()
    assert reset["status"] == "idle"
    assert reset["progress"] == 0.0


def test_list_vehicles_includes_created_vehicle(client):
    created = client.post("/vehicles", json={"name": "Truck X", "origin": "Guwahati", "destination": "Tezpur"}).json()
    listed = client.get("/vehicles").json()
    assert any(v["id"] == created["id"] for v in listed)


def test_get_unknown_vehicle_returns_404(client):
    resp = client.get("/vehicles/vehicle_does_not_exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Part 15C: GET /segments/{id}/ml-risk -- isolated, advisory ML risk signal
# ---------------------------------------------------------------------------


@pytest.fixture
def ml_enabled(monkeypatch):
    """Enables the ML risk signal for the duration of one test only.
    ML_RISK_ENABLED is False everywhere else in this suite (the shipped
    default) -- monkeypatch guarantees it reverts even if the test fails."""
    import app.config as config
    from app.core import ml_risk_signal

    monkeypatch.setattr(config, "ML_RISK_ENABLED", True)
    ml_risk_signal.clear_artifact_cache()
    yield
    ml_risk_signal.clear_artifact_cache()


def _first_segment_with_ml_features(client) -> str:
    """A real segment with non-null slope_deg/elevation_m -- required for
    the ML service to produce available=True (see ml_risk_signal.py)."""
    segments = client.get("/segments").json()
    return next(s["id"] for s in segments if s["slope_deg"] is not None and s["elevation_m"] is not None)


def test_ml_risk_endpoint_disabled_by_default_returns_clean_unavailable(client):
    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/segments/{seg_id}/ml-risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["score"] is None
    assert body["model_version"] is None
    assert "disabled" in body["reason"].lower()


def test_ml_risk_endpoint_unknown_segment_returns_404(client):
    resp = client.get("/segments/does_not_exist/ml-risk")
    assert resp.status_code == 404


def test_ml_risk_endpoint_enabled_real_segment_real_model(client, ml_enabled):
    """The primary happy-path test -- NOT mocked: a real corridor segment
    scored by the real saved v2 (17-feature) Random Forest."""
    seg_id = _first_segment_with_ml_features(client)
    resp = client.get(f"/segments/{seg_id}/ml-risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["reason"] == "ok"
    assert body["score"] is not None
    assert 0.0 <= body["score"] <= 1.0
    note = body["methodology_note"].lower()
    assert "probability" in note and "not" in note
    assert "calibrated" in note


def test_ml_risk_endpoint_returns_model_version(client, ml_enabled):
    seg_id = _first_segment_with_ml_features(client)
    body = client.get(f"/segments/{seg_id}/ml-risk").json()
    assert body["model_version"] == "part15a_segment_year_v2_17feature"


def test_ml_risk_endpoint_returns_feature_schema_version(client, ml_enabled):
    seg_id = _first_segment_with_ml_features(client)
    body = client.get(f"/segments/{seg_id}/ml-risk").json()
    assert isinstance(body["feature_schema_version"], str)
    assert len(body["feature_schema_version"]) > 0


def test_ml_risk_endpoint_deterministic_across_repeated_requests(client, ml_enabled):
    seg_id = _first_segment_with_ml_features(client)
    first = client.get(f"/segments/{seg_id}/ml-risk").json()
    second = client.get(f"/segments/{seg_id}/ml-risk").json()
    assert first["score"] == second["score"]
    assert first["model_version"] == second["model_version"]
    assert first["feature_schema_version"] == second["feature_schema_version"]


def test_ml_risk_endpoint_artifact_unavailable_returns_clean_unavailable(client, ml_enabled, monkeypatch, tmp_path):
    import app.config as config
    from app.core import ml_risk_signal

    monkeypatch.setattr(config, "ML_ARTIFACT_DIR", tmp_path / "missing_artifact_dir")
    ml_risk_signal.clear_artifact_cache()

    seg_id = client.get("/segments").json()[0]["id"]
    resp = client.get(f"/segments/{seg_id}/ml-risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["score"] is None
    assert "unavailable" in body["reason"].lower()


def test_ml_risk_endpoint_never_asserts_score_is_a_probability(client, ml_enabled):
    """The exact wording rule (Part 15C section 3): any mention of
    "probability" in the response must be a NEGATION ("NOT a calibrated
    probability"), never an assertion that the score IS one -- same
    convention already enforced for /routes/calculate-risk-aware's
    `reasons` in test_calculate_risk_aware_route above."""
    seg_id = _first_segment_with_ml_features(client)
    body = client.get(f"/segments/{seg_id}/ml-risk").json()

    banned_positive_claims = [
        "probability of landslide", "percentage chance", "likelihood percentage", "chance of landslide",
    ]
    full_text = str(body).lower()
    for phrase in banned_positive_claims:
        assert phrase not in full_text

    assert "probability" not in full_text or "not a calibrated probability" in full_text


def test_existing_route_calculation_unaffected_by_ml_risk_endpoint_disabled(client):
    """Regression protection (ML disabled, the shipped default): calling
    the new endpoint must not change subsequent route-calculation output
    in any way -- risk scores, route segments, distance, travel time, or
    the reported safety outcome."""
    before = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()

    for seg_id in before["fastest_route"]["segment_ids"][:20]:
        client.get(f"/segments/{seg_id}/ml-risk")

    after = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()

    assert before["outcome"] == after["outcome"]
    assert before["fastest_route"]["segment_ids"] == after["fastest_route"]["segment_ids"]
    assert before["fastest_route"]["total_distance_km"] == after["fastest_route"]["total_distance_km"]
    assert before["fastest_route"]["estimated_travel_time_min"] == after["fastest_route"]["estimated_travel_time_min"]
    assert before["fastest_route_risk"]["aggregate_risk_score"] == after["fastest_route_risk"]["aggregate_risk_score"]
    assert before["fastest_route_segment_risks"] == after["fastest_route_segment_risks"]
    if before["recommended_route"] is not None:
        assert before["recommended_route"]["segment_ids"] == after["recommended_route"]["segment_ids"]
        assert before["recommended_route_risk"] == after["recommended_route_risk"]


def test_existing_route_calculation_unaffected_by_ml_risk_endpoint_enabled(client, ml_enabled):
    """Same regression check, but with ML_RISK_ENABLED=True and the ML
    endpoint actually invoked (real model, real segments) -- confirms
    even a SUCCESSFUL ML inference call has no effect on routing/risk."""
    before = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()

    for seg_id in before["fastest_route"]["segment_ids"][:20]:
        client.get(f"/segments/{seg_id}/ml-risk")

    after = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()

    assert before["outcome"] == after["outcome"]
    assert before["fastest_route"]["segment_ids"] == after["fastest_route"]["segment_ids"]
    assert before["fastest_route"]["total_distance_km"] == after["fastest_route"]["total_distance_km"]
    assert before["fastest_route"]["estimated_travel_time_min"] == after["fastest_route"]["estimated_travel_time_min"]
    assert before["fastest_route_risk"]["aggregate_risk_score"] == after["fastest_route_risk"]["aggregate_risk_score"]
    assert before["fastest_route_segment_risks"] == after["fastest_route_segment_risks"]


def test_existing_segment_risk_endpoints_unaffected_by_ml_risk_endpoint(client, ml_enabled):
    seg_id = _first_segment_with_ml_features(client)

    risk_before = client.get(f"/segments/{seg_id}/risk").json()
    risk_aware_before = client.get(f"/segments/{seg_id}/risk-aware").json()

    client.get(f"/segments/{seg_id}/ml-risk")

    risk_after = client.get(f"/segments/{seg_id}/risk").json()
    risk_aware_after = client.get(f"/segments/{seg_id}/risk-aware").json()

    assert risk_before == risk_after
    assert risk_aware_before == risk_aware_after


def test_vehicle_reroutes_around_real_hazard_ahead(client, clean_hazards):
    """End-to-end Part 8+9 integration via the real HTTP API."""
    import time

    on_route_ids = [s["id"] for s in client.get("/segments").json() if s["name"] == "Doimara-Nichiphu"]
    created = client.post("/vehicles", json={"name": "Truck 1", "origin": "Bhalukpong", "destination": "Bomdila"}).json()
    route_segment_ids = set(created["current_route"]["segment_ids"])
    affected = [sid for sid in on_route_ids if sid in route_segment_ids]
    assert affected, "expected the real route to use a real Doimara-Nichiphu segment"

    client.post(f"/vehicles/{created['id']}/start")
    client.post("/hazards/simulate", json={"type": "road_blockage", "severity": "blocking", "affected_segment_ids": affected})
    time.sleep(0.5)

    polled = client.get(f"/vehicles/{created['id']}").json()
    assert polled["status"] in ("rerouting", "en_route")
    assert not (set(polled["current_route"]["segment_ids"]) & set(affected))


# ---------------------------------------------------------------------------
# Part 15E: routing/reroute isolation regression -- the architectural
# guarantee that GET /segments/{id}/ml-risk (Part 15C) and ML_RISK_ENABLED
# (Part 15B) have ZERO effect on route calculation or hazard-driven
# rerouting, proven with a real A/B comparison inside ONE test (same
# TestClient/app instance, ML flag flipped mid-test via monkeypatch) rather
# than just asserting "nothing changed after enabling ML" in isolation.
# ---------------------------------------------------------------------------


def _without_route_id(route):
    """Strips the two fields that legitimately differ between any two
    otherwise-identical route calculations regardless of ML state: a
    random route_id (uuid4, see app/models/route.py) and its created_at
    wall-clock timestamp. Everything else must match exactly."""
    if route is None:
        return None
    return {k: v for k, v in route.items() if k not in ("route_id", "created_at")}


def test_route_calculation_identical_ml_disabled_vs_enabled(client, monkeypatch):
    """Section 10's routing isolation regression: the SAME
    /routes/calculate-risk-aware call, once with ML_RISK_ENABLED=False and
    once with it genuinely True (and the ML endpoint actually exercised for
    every segment on the route in between), must produce byte-identical
    route geometry, segment_ids, distance, ETA, aggregate/per-segment risk
    scores, risk levels, and safety outcome -- only route_id (a random
    UUID per calculation, unrelated to ML) is excluded from the diff."""
    import app.config as config
    from app.core import ml_risk_signal

    monkeypatch.setattr(config, "ML_RISK_ENABLED", False)
    disabled = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()

    monkeypatch.setattr(config, "ML_RISK_ENABLED", True)
    ml_risk_signal.clear_artifact_cache()
    for seg_id in disabled["fastest_route"]["segment_ids"][:30]:
        resp = client.get(f"/segments/{seg_id}/ml-risk")
        assert resp.status_code == 200
        assert resp.json()["available"] is True  # confirm ML actually ran, not silently skipped

    enabled = client.post("/routes/calculate-risk-aware", json={"origin": "Guwahati", "destination": "Tawang"}).json()
    ml_risk_signal.clear_artifact_cache()

    assert disabled["outcome"] == enabled["outcome"]
    assert disabled["safer_alternative_selected"] == enabled["safer_alternative_selected"]
    assert disabled["unsafe_segments_in_fastest_route"] == enabled["unsafe_segments_in_fastest_route"]
    assert _without_route_id(disabled["fastest_route"]) == _without_route_id(enabled["fastest_route"])
    assert disabled["fastest_route_risk"] == enabled["fastest_route_risk"]
    assert disabled["fastest_route_segment_risks"] == enabled["fastest_route_segment_risks"]
    assert _without_route_id(disabled["recommended_route"]) == _without_route_id(enabled["recommended_route"])
    assert disabled["recommended_route_risk"] == enabled["recommended_route_risk"]
    assert disabled["recommended_route_segment_risks"] == enabled["recommended_route_segment_risks"]
    assert disabled["reasons"] == enabled["reasons"]


def test_reroute_decision_identical_ml_disabled_vs_enabled(client, clean_hazards, monkeypatch):
    """Section 11's reroute isolation regression: repeats the real
    hazard -> reroute scenario (same as
    test_evaluate_disruption_real_corridor_reroutes_after_hazard above)
    once with ML disabled and once with it enabled and actually queried,
    and confirms the REROUTE decision is identical either way. Each run
    creates its own hazard (a fresh random id/timestamp each time, by
    design -- see app/models/hazard.py), so `active_hazard_ids` and the
    two runs' own route_ids are excluded from the diff; everything that
    should be decision-relevant is compared exactly."""
    import app.config as config
    from app.core import ml_risk_signal

    on_route = [s["id"] for s in client.get("/segments").json() if s["name"] == "Doimara-Nichiphu"]
    assert on_route

    def run_scenario():
        baseline = client.post("/routes/evaluate-disruption", json={"origin": "Bhalukpong", "destination": "Bomdila"}).json()
        hazard = client.post(
            "/hazards/simulate",
            json={"type": "road_blockage", "severity": "blocking", "affected_segment_ids": on_route},
        ).json()
        disrupted = client.post(
            "/routes/evaluate-disruption",
            json={
                "origin": "Bhalukpong",
                "destination": "Bomdila",
                "previous_route_id": baseline["recommended_route"]["route_id"],
            },
        ).json()
        client.post(f"/hazards/{hazard['id']}/clear")
        return baseline, disrupted

    monkeypatch.setattr(config, "ML_RISK_ENABLED", False)
    baseline_disabled, disrupted_disabled = run_scenario()

    monkeypatch.setattr(config, "ML_RISK_ENABLED", True)
    ml_risk_signal.clear_artifact_cache()
    for seg_id in on_route:
        resp = client.get(f"/segments/{seg_id}/ml-risk")
        assert resp.json()["available"] is True
    baseline_enabled, disrupted_enabled = run_scenario()
    ml_risk_signal.clear_artifact_cache()

    assert baseline_disabled["outcome"] == baseline_enabled["outcome"] == "continue"
    assert disrupted_disabled["outcome"] == disrupted_enabled["outcome"] == "reroute"

    assert _without_route_id(disrupted_disabled["recommended_route"]) == _without_route_id(disrupted_enabled["recommended_route"])
    assert disrupted_disabled["recommended_route_risk"] == disrupted_enabled["recommended_route_risk"]
    assert disrupted_disabled["affected_segment_ids"] == disrupted_enabled["affected_segment_ids"] == sorted(on_route)
    assert disrupted_disabled["eta_change_min"] == disrupted_enabled["eta_change_min"]
    assert disrupted_disabled["reason"] == disrupted_enabled["reason"]


def test_ml_risk_lookup_does_not_mutate_state_store(client, ml_enabled):
    """Confirms fetching the ML signal is read-only at the StateStore
    level too, not just "the next route calculation happens to match":
    the real, live segment objects (which routing_engine.build_graph()
    reads current_risk_score/etc. from) are unchanged after the call."""
    from app.store.state_store import state_store

    segment_id = _first_segment_with_ml_features(client)
    before = state_store.get_segment(segment_id).model_dump()

    client.get(f"/segments/{segment_id}/ml-risk")

    after = state_store.get_segment(segment_id).model_dump()
    assert before == after
