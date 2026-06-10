"""Test fixtures: a real-socket FakeLucon plus TestClients wired to it.

Each client runs the full app lifespan, so the gateway's supervisor thread
actually connects to the FakeLucon over UDP — exercising the genuine transport
path, not a mock.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lucon.testing import FakeLucon
from lucon_api.app import create_app
from lucon_api.config import Settings
from lucon_api.gateway import Gateway


@pytest.fixture
def fake() -> Iterator[FakeLucon]:
    """A started in-process LUCON simulator (channels 1-4 online by default)."""
    server = FakeLucon()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _wait_ready(app: FastAPI, timeout: float = 5.0) -> None:
    gateway: Gateway = app.state.gateway
    end = time.monotonic() + timeout
    while not gateway.ready and time.monotonic() < end:
        time.sleep(0.02)
    if not gateway.ready:
        raise RuntimeError("gateway did not connect to FakeLucon within timeout")


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(fake: FakeLucon) -> Iterator[ClientFactory]:
    """Factory building TestClients against the shared FakeLucon.

    ``wait=False`` skips the readiness wait (for disconnected/503 scenarios), and
    any Settings field can be overridden (e.g. ``api_key=...``, ``enable_raw=True``,
    or a bogus ``host``/``port`` to simulate an unreachable device).
    """
    managers: list[TestClient] = []

    def _make(*, wait: bool = True, **overrides: Any) -> TestClient:
        host, port = fake.address
        params: dict[str, Any] = {
            "host": host,
            "port": port,
            # Loopback fake answers in sub-ms, so keep the timeout/backoff tiny:
            # the 503/504 paths resolve in ~0.1s instead of ~0.6s, and the
            # reconnect supervisor doesn't thrash on teardown.
            "timeout": 0.1,
            "retries": 0,
            "request_deadline": 2.0,
            "reconnect_backoff_initial": 0.02,
            "reconnect_backoff_max": 0.05,
        }
        params.update(overrides)
        app = create_app(Settings(**params))
        client = TestClient(app)
        client.__enter__()
        managers.append(client)
        if wait:
            _wait_ready(app)
        return client

    try:
        yield _make
    finally:
        for client in managers:
            client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    """A ready TestClient connected to the FakeLucon."""
    return make_client()


def poll_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Poll ``predicate`` until true or timeout; returns the final truthiness."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
