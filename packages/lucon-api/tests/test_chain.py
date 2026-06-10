"""General / master (``/v1/chain``) endpoints: identity, persistence, network, config."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon

_SERIAL = "FAKE-SERIAL-0001"


def test_chain_curated_identity(client: TestClient) -> None:
    body = client.get("/v1/chain").json()
    assert body["serial"] == _SERIAL
    assert body["firmware"] == "LUCON 4C-20A-V v1.0"
    assert body["mac"] == "00:11:22:33:44:55"
    assert body["controller_offset"] == 0
    assert body["persisted"] is True
    assert body["offsets"] == [0]
    assert body["online_channels"] == [1, 2, 3, 4]


def test_chain_full_detail(client: TestClient, fake: FakeLucon) -> None:
    # Provide values the bare fake leaves empty so the full read parses cleanly.
    fake.set_read(0, "USU", "24000")
    fake.set_read(0, "RCP", "RevA")
    fake.set_read(0, "RPP", "RevB")
    fake.set_read(0, "M", "ok")  # error buffer (the bare fake returns empty)
    body = client.get("/v1/chain", params={"detail": "full"}).json()
    assert body["ip"] == "0.0.0.0"
    assert body["udp_port"] == 8000
    assert body["bootloader"] == "1.0"
    assert body["supply_voltage_mv"] == 24000
    assert body["pcb_revision_control"] == "RevA"
    assert body["unavailable"] == []


def test_chain_granular_field(client: TestClient) -> None:
    body = client.get("/v1/chain/serial").json()
    assert body == {"field": "serial", "value": _SERIAL}


def test_chain_unknown_field_404(client: TestClient) -> None:
    assert client.get("/v1/chain/bogus").status_code == 404


def test_chain_save(client: TestClient) -> None:
    resp = client.post("/v1/chain/save", json={"scope": 1})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_chain_save_scope_out_of_range_422(client: TestClient) -> None:
    assert client.post("/v1/chain/save", json={"scope": 3}).status_code == 422


def test_config_toggle_current_tenths(client: TestClient) -> None:
    assert client.get("/v1/chain/config").json() == {"current_tenths": False}
    patched = client.patch("/v1/chain/config", json={"current_tenths": True})
    assert patched.status_code == 200
    assert patched.json() == {"current_tenths": True}
    assert client.get("/v1/chain/config").json() == {"current_tenths": True}


def test_restart_requires_confirmation(client: TestClient) -> None:
    # Missing confirm field -> schema validation 422.
    assert client.post("/v1/chain/restart", json={}).status_code == 422
    # Wrong confirm -> confirmation_required 422.
    bad = client.post("/v1/chain/restart", json={"confirm": "nope"})
    assert bad.status_code == 422
    assert bad.json()["error"]["type"] == "confirmation_required"


def test_restart_with_correct_serial(client: TestClient) -> None:
    resp = client.post("/v1/chain/restart", json={"confirm": _SERIAL})
    assert resp.status_code == 200
    assert "reconnect" in resp.json()["warning"].lower()


def test_factory_reset_requires_confirmation(client: TestClient) -> None:
    bad = client.post("/v1/chain/factory-reset", json={"scope": 0, "confirm": "x"})
    assert bad.status_code == 422
    assert bad.json()["error"]["type"] == "confirmation_required"


def test_network_ip_only_uses_serial_checked(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put(
        "/v1/chain/network", json={"ip": "192.168.0.99", "confirm": _SERIAL}
    )
    assert resp.status_code == 200
    assert "orphan" in resp.json()["warning"].lower()
    # set_ip_checked sends S00SIP|ip|serial -> stored in the fake's memory.
    assert fake._memory[(0, "SIP")] == ("192.168.0.99", _SERIAL)


def test_network_requires_at_least_one_field(client: TestClient) -> None:
    resp = client.put("/v1/chain/network", json={"confirm": _SERIAL})
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "invalid_value"


def test_network_multi_field_still_uses_serial_checked_ip(client: TestClient, fake: FakeLucon) -> None:
    # Regression: an IP change combined with another field must STILL use the
    # firmware serial-checked command (SIP), not plain set_ip.
    resp = client.put(
        "/v1/chain/network",
        json={"ip": "10.0.0.5", "controller_offset": 1, "confirm": _SERIAL},
    )
    assert resp.status_code == 200
    assert fake._memory[(0, "SIP")] == ("10.0.0.5", _SERIAL)
    assert (0, "IP") not in fake._memory  # plain unchecked set_ip never used
    assert fake._memory[(0, "CO")] == ("1",)
