"""Read-only topology endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon
from tests.conftest import ClientFactory


def test_list_controllers(client: TestClient) -> None:
    body = client.get("/v1/controllers").json()
    assert body == [{"offset": 0, "is_master": True, "channels": [1, 2, 3, 4]}]


def test_get_controller(client: TestClient) -> None:
    body = client.get("/v1/controllers/0").json()
    assert body == {"offset": 0, "is_master": True, "channels": [1, 2, 3, 4]}


def test_get_controller_missing_offset_404(client: TestClient) -> None:
    resp = client.get("/v1/controllers/5")
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found"


def test_get_controller_offset_out_of_range_422(client: TestClient) -> None:
    assert client.get("/v1/controllers/99").status_code == 422


def test_multi_controller_chain(make_client: ClientFactory, fake: FakeLucon) -> None:
    # Reconfigure the fake to report two controllers' worth of online channels.
    fake._online_channels = {1, 2, 3, 4, 5, 6, 7, 8}
    client = make_client()
    body = client.get("/v1/controllers").json()
    assert [c["offset"] for c in body] == [0, 1]
    assert body[1] == {"offset": 1, "is_master": False, "channels": [5, 6, 7, 8]}
