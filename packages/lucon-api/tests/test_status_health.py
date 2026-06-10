"""Root status and health/readiness probes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ClientFactory


def test_healthz_always_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ok_when_connected(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True}


def test_root_status_reports_cached_identity(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["service"] == "lucon-api"
    assert body["ready"] is True
    assert body["serial"] == "FAKE-SERIAL-0001"
    assert body["firmware"] == "LUCON 4C-20A-V v1.0"
    assert body["controller_offset"] == 0
    assert body["offsets"] == [0]
    assert body["online_channels"] == [1, 2, 3, 4]


def test_readyz_503_when_device_unreachable(make_client: ClientFactory) -> None:
    # Point at a port with no listener; the gateway never becomes ready.
    client = make_client(wait=False, host="127.0.0.1", port=9)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"ready": False}


def test_healthz_open_without_api_key(make_client: ClientFactory) -> None:
    client = make_client(api_key="secret")
    # Liveness is unversioned and must not require the key.
    assert client.get("/healthz").status_code == 200
