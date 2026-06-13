"""Unsolicited event fan-out: replay buffer and SSE stream (ADR-0004)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lucon.testing import FakeLucon
from tests.conftest import poll_until


def test_recent_buffers_injected_notices(client: TestClient, fake: FakeLucon) -> None:
    fake.inject_error("overtemp 85C")
    fake.inject_status("hello")  # not 'RUNNING' -> no reconnect side effect

    assert poll_until(lambda: len(client.get("/v1/events/recent").json()) >= 2)
    events = client.get("/v1/events/recent").json()
    kinds = {e["kind"] for e in events}
    assert kinds == {"error", "status"}
    error = next(e for e in events if e["kind"] == "error")
    assert "overtemp" in error["message"]
    assert error["raw"] is not None


def test_recent_after_id_filters(client: TestClient, fake: FakeLucon) -> None:
    fake.inject_error("first")
    assert poll_until(lambda: len(client.get("/v1/events/recent").json()) >= 1)
    first_id = client.get("/v1/events/recent").json()[0]["id"]
    fake.inject_error("second")
    assert poll_until(
        lambda: any(
            e["message"] == "second"
            for e in client.get(
                "/v1/events/recent", params={"after_id": first_id}
            ).json()
        )
    )
    filtered = client.get("/v1/events/recent", params={"after_id": first_id}).json()
    assert all(e["id"] > first_id for e in filtered)


def test_sse_framing() -> None:
    # Unit-test the SSE wire framing directly. The live stream is intentionally
    # NOT iterated through TestClient: an infinite text/event-stream deadlocks
    # the test portal on close. Fan-out/replay is covered via /events/recent.
    from lucon_api.routes.events import _format_sse

    frame = _format_sse(
        {"id": 7, "ts": 1.5, "kind": "error", "message": "overtemp", "raw": ":E x"}
    )
    assert "id: 7" in frame
    assert "event: error" in frame
    assert '"message": "overtemp"' in frame
    assert frame.endswith("\n\n")


def test_running_status_triggers_reconnect_and_stays_ready(
    client: TestClient, fake: FakeLucon
) -> None:
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    before = gateway.connect_count
    fake.inject_status("RUNNING")
    # The :S RUNNING schedules a reconnect; the fake is still up, so the gateway
    # must perform another successful connect and converge back to ready.
    assert poll_until(lambda: gateway.connect_count > before, timeout=3.0)
    assert poll_until(lambda: gateway.ready, timeout=2.0)
