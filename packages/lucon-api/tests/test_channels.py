"""Per-channel operations: mode, limits, trigger config, reads, save/reset."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon


# --- mode ---------------------------------------------------------------


def test_set_continuous_mode(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put("/v1/channels/1/mode", json={"mode": "continuous", "ma": 100})
    assert resp.status_code == 200
    assert fake._memory[(1, "MC")] == ("100",)


def test_set_pulse_mode_sends_microseconds(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put(
        "/v1/channels/1/mode",
        json={"mode": "pulse", "ma": 8000, "delay_us": 100, "duration_us": 500},
    )
    assert resp.status_code == 200
    assert fake._memory[(1, "MDU")] == ("8000", "100", "500")


def test_set_switch_mode(client: TestClient, fake: FakeLucon) -> None:
    assert (
        client.put(
            "/v1/channels/2/mode", json={"mode": "switch", "ma": 5000}
        ).status_code
        == 200
    )
    assert fake._memory[(2, "MT")] == ("5000",)


def test_set_none_mode(client: TestClient, fake: FakeLucon) -> None:
    assert client.put("/v1/channels/1/mode", json={"mode": "none"}).status_code == 200
    assert fake._memory[(1, "MN")] == ()


def test_get_mode_enriched(client: TestClient) -> None:
    client.put("/v1/channels/1/mode", json={"mode": "continuous", "ma": 100})
    body = client.get("/v1/channels/1/mode").json()
    assert body["mode"] == "continuous"
    assert body["ma"] == 100.0


def test_continuous_over_3a_rejected(client: TestClient) -> None:
    resp = client.put("/v1/channels/1/mode", json={"mode": "continuous", "ma": 5000})
    assert resp.status_code == 422


def test_pulse_missing_duration_rejected(client: TestClient) -> None:
    resp = client.put(
        "/v1/channels/1/mode", json={"mode": "pulse", "ma": 8000, "delay_us": 100}
    )
    assert resp.status_code == 422


def test_unknown_mode_discriminator_rejected(client: TestClient) -> None:
    assert client.put("/v1/channels/1/mode", json={"mode": "strobe"}).status_code == 422


# --- limits -------------------------------------------------------------


def test_set_and_read_limits(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put(
        "/v1/channels/1/limits",
        json={"continuous_ma": 2000, "pulse_ma": 15000, "voltage_mv": 12000},
    )
    assert resp.status_code == 200
    assert fake._memory[(1, "L")] == ("2000",)
    assert fake._memory[(1, "LP")] == ("15000",)
    assert fake._memory[(1, "V")] == ("12000",)
    body = client.get("/v1/channels/1/limits").json()
    assert body == {"continuous_ma": 2000.0, "pulse_ma": 15000.0, "voltage_mv": 12000}


def test_partial_limits_only_writes_provided(
    client: TestClient, fake: FakeLucon
) -> None:
    assert (
        client.put("/v1/channels/3/limits", json={"voltage_mv": 30000}).status_code
        == 200
    )
    assert fake._memory[(3, "V")] == ("30000",)
    assert (3, "L") not in fake._memory
    assert (3, "LP") not in fake._memory


def test_voltage_limit_out_of_range_rejected(client: TestClient) -> None:
    assert (
        client.put("/v1/channels/1/limits", json={"voltage_mv": 999}).status_code == 422
    )


# --- trigger config -----------------------------------------------------


def test_set_and_read_trigger_input(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put(
        "/v1/channels/1/trigger/input",
        json={
            "pulse_edge": "falling",
            "switch_active_high": True,
            "switch_current_ma": 3000,
        },
    )
    assert resp.status_code == 200
    assert fake._memory[(1, "I")] == ("1",)  # falling -> "1"
    assert fake._memory[(1, "ST")] == ("1",)
    assert fake._memory[(1, "SC")] == ("3000",)
    body = client.get("/v1/channels/1/trigger/input").json()
    assert body == {
        "pulse_edge": "falling",
        "switch_active_high": True,
        "switch_current_ma": 3000.0,
    }


def test_set_and_read_trigger_output(client: TestClient, fake: FakeLucon) -> None:
    resp = client.put(
        "/v1/channels/1/trigger/output",
        json={
            "enabled": True,
            "polarity": "rising",
            "source": "lighting",
            "type": "while_lit",
            "delay_us": 50,
            "length_us": 200,
        },
    )
    assert resp.status_code == 200
    assert fake._memory[(1, "O")] == ("1",)
    assert fake._memory[(1, "OTE")] == ("0",)  # rising -> "0"
    assert fake._memory[(1, "OTS")] == (
        "1",
    )  # lighting SET token -> "1" (confirmed fw 0.5.0)
    assert fake._memory[(1, "OTT")] == ("1",)  # while_lit -> "1"
    body = client.get("/v1/channels/1/trigger/output").json()
    assert body == {
        "enabled": True,
        "polarity": "rising",
        "source": "lighting",
        "type": "while_lit",
        "delay_us": 50,
        "length_us": 200,
    }


def test_output_polarity_both_rejected(client: TestClient) -> None:
    # 'both' is not a valid OutputPolarityName.
    resp = client.put("/v1/channels/1/trigger/output", json={"polarity": "both"})
    assert resp.status_code == 422


# --- reads --------------------------------------------------------------


def test_channel_summary_list(client: TestClient) -> None:
    rows = client.get("/v1/channels").json()
    assert [r["channel_num"] for r in rows] == [1, 2, 3, 4]
    assert rows[0]["mode"] == "continuous"
    assert rows[0]["temperature_c"] == 30.0


def test_channel_curated_detail(client: TestClient) -> None:
    body = client.get("/v1/channels/1").json()
    assert body["channel_num"] == 1
    assert body["offset"] == 0
    assert body["local_index"] == 1
    assert set(body["readings"]) >= {"mode", "temperature_c"}
    assert body["readings"]["mode"] == "continuous"


def test_channel_full_detail(client: TestClient) -> None:
    body = client.get("/v1/channels/1", params={"detail": "full"}).json()
    assert body["unavailable"] == []
    assert body["readings"]["mode"] == "continuous"
    assert body["readings"]["temperature_c"] == 30.0
    assert body["readings"]["output_source"] == "input"


def test_granular_field_read(client: TestClient) -> None:
    body = client.get("/v1/channels/1/temperature_c").json()
    assert body == {"field": "temperature_c", "value": 30.0}


def test_unknown_field_404(client: TestClient) -> None:
    assert client.get("/v1/channels/1/bogus").status_code == 404


# --- save / reset -------------------------------------------------------


def test_save_channel(client: TestClient, fake: FakeLucon) -> None:
    assert client.post("/v1/channels/1/save").status_code == 200
    assert (1, "S") in fake._memory


def test_reset_channel(client: TestClient, fake: FakeLucon) -> None:
    assert client.post("/v1/channels/1/reset").status_code == 200
    assert (1, "FR") in fake._memory


# --- addressing ---------------------------------------------------------


def test_offline_channel_404(client: TestClient) -> None:
    resp = client.get("/v1/channels/50")
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found"


def test_channel_out_of_range_422(client: TestClient) -> None:
    assert client.get("/v1/channels/0").status_code == 422
    assert client.get("/v1/channels/97").status_code == 422
