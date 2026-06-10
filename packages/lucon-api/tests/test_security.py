"""API-key guard and the opt-in raw escape hatch."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon
from tests.conftest import ClientFactory


def test_api_key_required_when_configured(make_client: ClientFactory) -> None:
    client = make_client(api_key="secret")
    assert client.get("/v1/channels/1").status_code == 401
    assert client.get("/v1/channels/1", headers={"X-API-Key": "secret"}).status_code == 200
    assert (
        client.get("/v1/channels/1", headers={"Authorization": "Bearer secret"}).status_code == 200
    )


def test_api_key_wrong_rejected(make_client: ClientFactory) -> None:
    client = make_client(api_key="secret")
    resp = client.get("/v1/channels/1", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "unauthorized"


def test_no_key_means_open(client: TestClient) -> None:
    # Default fixture has no api_key configured.
    assert client.get("/v1/channels/1").status_code == 200


def test_raw_disabled_by_default(client: TestClient) -> None:
    resp = client.post("/v1/raw", json={"verb": "R", "channel": 0, "cmd": "F"})
    assert resp.status_code == 404
    # Disabled means invisible: don't leak the feature or its env toggle.
    assert "LUCON_ENABLE_RAW" not in resp.text
    assert "raw" not in resp.json()["error"]["message"].lower()


def test_raw_read_when_enabled(make_client: ClientFactory) -> None:
    client = make_client(enable_raw=True)
    resp = client.post("/v1/raw", json={"verb": "R", "channel": 0, "cmd": "F"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "read_reply"
    assert body["values"] == ["LUCON 4C-20A-V v1.0"]


def test_raw_set_when_enabled(make_client: ClientFactory, fake: FakeLucon) -> None:
    client = make_client(enable_raw=True)
    resp = client.post(
        "/v1/raw", json={"verb": "S", "channel": 1, "cmd": "MC", "values": ["250"]}
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "set_ack"
    assert fake._memory[(1, "MC")] == ("250",)
