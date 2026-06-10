"""Error mapping: device rejection (502), timeout (504), unavailable (503)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon
from tests.conftest import ClientFactory, poll_until


def test_device_rejection_maps_to_502(client: TestClient, fake: FakeLucon) -> None:
    fake.fail_next("parameter out of range", channel=1, cmd="MC")
    resp = client.put("/v1/channels/1/mode", json={"mode": "continuous", "ma": 100})
    assert resp.status_code == 502
    body = resp.json()["error"]
    assert body["type"] == "device_rejected"
    assert body["device_message"] == "parameter out of range"
    assert body["raw"] is not None


def test_device_timeout_maps_to_504(client: TestClient, fake: FakeLucon) -> None:
    fake.stop()  # device goes silent mid-session
    resp = client.get("/v1/channels/1/temperature_c")
    assert resp.status_code == 504
    assert resp.json()["error"]["type"] == "device_timeout"


def test_disconnected_maps_to_503(make_client: ClientFactory) -> None:
    client = make_client(wait=False, host="127.0.0.1", port=9)
    resp = client.put("/v1/channels/1/mode", json={"mode": "none"})
    assert resp.status_code == 503
    assert resp.json()["error"]["type"] == "device_unavailable"
    assert resp.headers.get("Retry-After") is not None


def test_timeout_marks_gateway_unhealthy(client: TestClient, fake: FakeLucon) -> None:
    # Regression: a device timeout must flip the gateway out of 'ready' so the
    # supervisor reconnects — otherwise it stays ready=True and 504s forever.
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    assert gateway.ready
    fake.stop()
    assert client.get("/v1/channels/1/temperature_c").status_code == 504
    assert poll_until(lambda: not gateway.ready, timeout=3.0)


def test_malformed_device_value_maps_to_502(client: TestClient, fake: FakeLucon) -> None:
    # Regression: a non-numeric device reply is a device-protocol error (502),
    # not a client value error (422).
    fake.set_read(0, "UDP", "not-a-number")
    resp = client.get("/v1/chain/udp_port")
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "device_protocol_error"
