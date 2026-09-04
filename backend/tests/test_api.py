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
