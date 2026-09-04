import pytest

from app.core.routing_engine import build_graph
from app.data.demo_locations import DEMO_LOCATIONS
from app.data.network_loader import load_network

# The 7 demonstration locations, in corridor order. These are names, not
# node ids — the real OSM graph's node ids are generated from coordinates
# (see osm_geojson_loader.py), not hand-picked slugs. Use
# routing_engine.resolve_location(nodes, name) to get the actual node.
CORRIDOR_ORDER = [loc["name"] for loc in DEMO_LOCATIONS]


@pytest.fixture(scope="session")
def network():
    """The real Guwahati-Tawang corridor OSM road network (see backend/app/data)."""
    return load_network()


@pytest.fixture(scope="session")
def graph(network):
    nodes, segments = network
    return build_graph(nodes, segments)
