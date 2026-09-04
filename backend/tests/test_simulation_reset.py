"""
Tests for Part 13's POST /simulation/reset -- restores the demo (hazards,
field reports, vehicles) to a known baseline without touching any static
source dataset.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store.state_store import state_store


@pytest.fixture(scope="module", autouse=True)
def loaded_store():
    state_store.load()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_reset_clears_hazards_field_reports_and_vehicles(client):
    seg_id = client.get("/segments").json()[0]["id"]

    client.post("/hazards/simulate", json={"type": "heavy_rain", "severity": "major", "affected_segment_ids": [seg_id]})
    client.post("/vehicles", json={"name": "Truck 1", "origin": "Guwahati", "destination": "Tezpur"})

    segs = client.get("/segments").json()
    target = next(s for s in segs if s["geometry"])
    point = target["geometry"][0]
    client.post(
        "/field-reports",
        json={
            "incident_type": "landslide",
            "severity": "major",
            "latitude": point["lat"],
            "longitude": point["lng"],
            "description": "Reset-test report",
        },
    )

    assert len(client.get("/hazards", params={"active_only": False}).json()) > 0
    assert len(client.get("/vehicles").json()) > 0
    assert len(client.get("/field-reports").json()) > 0

    resp = client.post("/simulation/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["segments_loaded"] > 500

    assert client.get("/hazards", params={"active_only": False}).json() == []
    assert client.get("/vehicles").json() == []
    assert client.get("/field-reports", params={"active_only": False}).json() == []


def test_reset_reloads_real_network_unchanged(client):
    resp = client.post("/simulation/reset")
    assert resp.status_code == 200

    network = client.get("/network").json()
    assert len(network["segments"]) > 500
    named = {n["name"] for n in network["nodes"] if n["name"]}
    assert "Guwahati" in named and "Tawang" in named


def test_reset_route_calculation_still_works_after_reset(client):
    client.post("/simulation/reset")
    resp = client.post("/routes/calculate", json={"origin": "Bhalukpong", "destination": "Bomdila"})
    assert resp.status_code == 200
    assert resp.json()["route"]["total_distance_km"] == pytest.approx(98.22, abs=0.5)
