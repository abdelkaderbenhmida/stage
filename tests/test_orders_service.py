import pytest
from starlette.testclient import TestClient

from tests.conftest import orders_app


@pytest.fixture
def client():
    return TestClient(orders_app())


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "orders"
    assert body["version"] == "1.0.0"


def test_livez(client):
    resp = client.get("/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_readyz_dev_no_vault(client):
    resp = client.get("/readyz")
    assert resp.status_code == 503


def test_health_alias(client):
    resp = client.get("/health")
    assert resp.status_code == 503


def test_list_orders(client):
    resp = client.get("/orders")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["user_id"] == 1